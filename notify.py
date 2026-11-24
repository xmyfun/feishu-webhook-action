#!/usr/bin/env python3
"""Feishu (Lark) custom-bot webhook sender for GitHub Actions.

Zero-dependency: Python 3 standard library only. No package install, no build step.

Interface (v1, frozen — see README):
  Transport:  webhook / webhooks / webhook_secret / timeout / retries
  Content:    msg_type (text|interactive) / title / content / card
              template / status / include_context / extra_elements
  Behaviour:  dry_run / fail_on_error
  Outputs:    result / sent / failed / body

Field names follow the Feishu custom-bot request body (`msg_type`, `content`,
`card`, `header.template`) so a reader of the Feishu docs needs no translation
table; transport-level names follow the common GitHub Action conventions
(`webhook`, `webhooks`, `fail_on_error`, `dry_run`, `timeout`).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Feishu card header colours accepted by the custom-bot API.
TEMPLATES = (
    "blue", "wathet", "turquoise", "green", "yellow", "orange",
    "red", "carmine", "violet", "purple", "indigo", "grey",
)
STATUS_TEMPLATE = {
    "success": "green",
    "failure": "red",
    "cancelled": "grey",
    "skipped": "grey",
}
TITLE_LIMIT = 40
MAX_BODY_BYTES = 20_000


class ActionError(Exception):
    """Invalid input: fail the step immediately with a clear message."""


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def flag(name: str, default: str = "false") -> bool:
    # An explicitly empty input falls back to the default (GitHub passes "" when
    # a `with:` entry is omitted for a composite action's optional input).
    raw = env(name) or default
    return raw.lower() in ("true", "1", "yes")


def warn(message: str) -> None:
    print(f"::warning::{message}")


# --------------------------------------------------------------------------- transport


def parse_targets(webhooks: str, webhook: str, secret: str) -> list[tuple[str, str]]:
    """Parse targets: multi-line `webhooks` wins, `webhook` is the fallback.

    Each line is `url` or `url|secret`. `secret` is the global default signing
    secret and applies to every line that carries no per-line secret.
    """
    targets: list[tuple[str, str]] = []
    for line in webhooks.splitlines():
        line = line.strip()
        if not line:
            continue
        url, _, line_secret = line.partition("|")
        url, line_secret = url.strip(), line_secret.strip()
        if url:
            targets.append((url, line_secret or secret))
    if not targets and webhook:
        targets.append((webhook, secret))
    return targets


def sign(body: dict, secret: str, timestamp: int | None = None) -> dict:
    """Attach a Feishu signature.

    sign = base64(HMAC-SHA256(key = timestamp + "\\n" + secret, msg = "")).
    `timestamp` / `sign` go into the request body, never into the URL.
    """
    if not secret:
        return body
    ts = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    signed = copy.deepcopy(body)
    signed["timestamp"] = ts
    signed["sign"] = base64.b64encode(digest).decode()
    return signed


def parse_response(raw: str) -> tuple[bool, str]:
    """Feishu reports errors as HTTP 200 + {"code": <non-zero>}; check the code."""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return False, f"non-JSON response: {raw[:200]}"
    code = payload.get("code", payload.get("StatusCode"))
    if code in (0, None, ""):
        return True, raw
    return False, f"code={code} msg={payload.get('msg', '')} body={raw[:200]}"


def send(
    url: str,
    body: dict,
    dry_run: bool = False,
    timeout: int = 15,
    retries: int = 0,
) -> tuple[bool, str]:
    """POST one payload; retry only network failures, 429 and 5xx."""
    data = json.dumps(body, ensure_ascii=False).encode()
    if dry_run:
        print("=== dry_run request body (not sent) ===")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return True, "dry_run"

    attempts = max(0, retries) + 1
    last_error = "no attempt made"
    for attempt in range(attempts):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                http_status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            http_status = exc.code
        except urllib.error.URLError as exc:
            last_error = f"URLError: {exc}"
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
                continue
            return False, last_error
        except Exception as exc:  # noqa: BLE001 - report any send failure, keep going
            return False, f"{type(exc).__name__}: {exc}"

        ok, detail = parse_response(raw)
        if ok:
            return True, raw
        last_error = detail
        retryable = http_status == 429 or 500 <= http_status < 600
        if retryable and attempt + 1 < attempts:
            time.sleep(2 ** attempt)
            continue
        return False, detail
    return False, last_error


# ---------------------------------------------------------------------------- content


def short_sha(sha: str) -> str:
    return sha[:7] if sha else "?"


def context_markdown() -> str:
    """GitHub run context as lark_md; empty when run outside Actions."""
    repo = env("GITHUB_REPOSITORY")
    ref = env("GITHUB_REF_NAME")
    sha = env("GITHUB_SHA")
    event = env("GITHUB_EVENT_NAME")
    run_id = env("GITHUB_RUN_ID")
    if not repo:
        return ""
    parts = [f"**Repository**: {repo}"]
    if ref:
        parts.append(f"**Ref**: {ref}")
    if sha:
        parts.append(f"**Commit**: `{short_sha(sha)}`")
    if event:
        parts.append(f"**Event**: {event}")
    if run_id:
        parts.append(f"**Run**: [#{run_id}](https://github.com/{repo}/actions/runs/{run_id})")
    return "\n".join(parts)


def parse_extra_elements(raw: str) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        warn(f"extra_elements is not valid JSON, ignored: {exc}")
        return []
    if not isinstance(data, list):
        warn("extra_elements must be a JSON array, ignored")
        return []
    return data


def resolve_template(raw_template: str, status: str) -> str:
    if raw_template:
        if raw_template in TEMPLATES:
            return raw_template
        warn(
            f"template '{raw_template}' is not a Feishu card colour; "
            f"falling back to blue (allowed: {', '.join(TEMPLATES)})"
        )
        return "blue"
    return STATUS_TEMPLATE.get(status.lower(), "blue")


def truncate_title(title: str) -> str:
    if len(title) <= TITLE_LIMIT:
        return title
    warn(f"title longer than {TITLE_LIMIT} characters, truncated for the card header")
    return title[: TITLE_LIMIT - 1] + "…"


def build_body() -> dict:
    """Assemble the Feishu request body from the inputs.

    Raises ActionError on invalid input combinations (no silent ignoring).
    """
    msg_type = env("INPUT_MSG_TYPE", "interactive").lower() or "interactive"
    if msg_type == "post":
        raise ActionError(
            "msg_type=post is not supported by this action; "
            "use msg_type=interactive with content, or msg_type=text"
        )
    if msg_type not in ("text", "interactive"):
        raise ActionError(f"msg_type must be text or interactive, got '{msg_type}'")

    title = env("INPUT_TITLE")
    content = env("INPUT_CONTENT")
    card_raw = env("INPUT_CARD")

    if card_raw:
        if msg_type != "interactive":
            raise ActionError("card can only be used with msg_type=interactive")
        if title or env("INPUT_TEMPLATE") or env("INPUT_EXTRA_ELEMENTS"):
            raise ActionError(
                "card is a complete card object: title / template / extra_elements "
                "cannot be combined with it"
            )
        if flag("INPUT_INCLUDE_CONTEXT", "true"):
            warn(
                "include_context is ignored when a complete card is supplied; "
                "append the context to the card yourself or set include_context=false"
            )
        try:
            card = json.loads(card_raw)
        except json.JSONDecodeError as exc:
            raise ActionError(f"card is not valid JSON: {exc}") from exc
        if not isinstance(card, dict):
            raise ActionError("card must be a JSON object (a complete Feishu card)")
        body = {"msg_type": "interactive", "card": card}
        if len(json.dumps(body, ensure_ascii=False).encode()) > MAX_BODY_BYTES:
            raise ActionError(f"card request body exceeds {MAX_BODY_BYTES} bytes")
        return body

    if msg_type == "text":
        text = content
        if flag("INPUT_INCLUDE_CONTEXT", "true"):
            ctx = context_markdown()
            if ctx:
                text = f"{text}\n\n{ctx}" if text else ctx
        if not text:
            raise ActionError("msg_type=text needs content (or GitHub context)")
        body = {"msg_type": "text", "content": {"text": text}}
        if len(json.dumps(body, ensure_ascii=False).encode()) > MAX_BODY_BYTES:
            raise ActionError(f"text request body exceeds {MAX_BODY_BYTES} bytes")
        return body

    # interactive: assemble a card. Element order is part of the contract:
    # [content, context, extra...]
    elements: list = []
    if content:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    if flag("INPUT_INCLUDE_CONTEXT", "true"):
        ctx = context_markdown()
        if ctx:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": ctx}})
    elements.extend(parse_extra_elements(env("INPUT_EXTRA_ELEMENTS")))

    if not title and not elements:
        raise ActionError(
            "msg_type=interactive needs at least one of title / content / "
            "extra_elements (or GitHub context)"
        )

    header = {"template": resolve_template(env("INPUT_TEMPLATE"), env("INPUT_STATUS"))}
    if title:
        header["title"] = {"tag": "plain_text", "content": truncate_title(title)}
    body = {"msg_type": "interactive", "card": {"header": header, "elements": elements}}
    return fit_body_size(body)


def fit_body_size(body: dict) -> dict:
    """Shrink the longest lark_md block until the request fits Feishu's limit."""
    while len(json.dumps(body, ensure_ascii=False).encode()) > MAX_BODY_BYTES:
        blocks = [
            element["text"]
            for element in body["card"]["elements"]
            if element.get("tag") == "div" and element.get("text", {}).get("tag") == "lark_md"
        ]
        if not blocks:
            raise ActionError(f"request body exceeds {MAX_BODY_BYTES} bytes and cannot be shrunk")
        longest = max(blocks, key=lambda block: len(block.get("content", "")))
        text = longest.get("content", "")
        if len(text) <= 1:
            raise ActionError(f"request body exceeds {MAX_BODY_BYTES} bytes and cannot be shrunk")
        warn(f"request body exceeds {MAX_BODY_BYTES} bytes, content truncated")
        longest["content"] = text[: max(1, int(len(text) * 0.8) - 1)] + "…"
    return body


# ----------------------------------------------------------------------------- outputs


def write_outputs(result: str, sent: int, failed: int, body: str = "") -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"result={result}\nsent={sent}\nfailed={failed}\n")
        if body:
            fh.write("body<<__FEISHU_BODY_EOF__\n")
            fh.write(body + "\n")
            fh.write("__FEISHU_BODY_EOF__\n")


# -------------------------------------------------------------------------------- main


def run() -> int:
    targets = parse_targets(
        env("INPUT_WEBHOOKS"), env("INPUT_WEBHOOK"), env("INPUT_WEBHOOK_SECRET")
    )
    if not targets:
        print(
            "No webhook configured (webhooks / webhook are both empty); "
            "skipping Feishu notification"
        )
        write_outputs("skipped", 0, 0)
        return 0

    body = build_body()
    body_json = json.dumps(body, ensure_ascii=False)
    dry_run = flag("INPUT_DRY_RUN")
    fail_on_error = flag("INPUT_FAIL_ON_ERROR")
    timeout = int(env("INPUT_TIMEOUT", "15") or 15)
    retries = int(env("INPUT_RETRIES", "0") or 0)
    if timeout <= 0:
        raise ActionError("timeout must be a positive number of seconds")
    if retries < 0:
        raise ActionError("retries must be >= 0")

    unsigned = [idx for idx, (_, secret) in enumerate(targets) if not secret]
    if unsigned and not dry_run:
        warn(
            f"{len(unsigned)} target(s) have no signing secret; if the bot has "
            "signature verification enabled, Feishu will reject them (code 19021)"
        )

    sent = 0
    failed = 0
    for idx, (url, secret) in enumerate(targets):
        ok, detail = send(url, sign(body, secret), dry_run, timeout, retries)
        if ok:
            if dry_run:
                print(f"[{idx}] dry_run ok")
            else:
                sent += 1
                print(f"[{idx}] sent successfully")
        else:
            failed += 1
            print(f"::warning::[{idx}] send failed (non-blocking): {detail}")

    result = "success" if failed == 0 else "failure"
    write_outputs(result, sent, failed, body_json if dry_run else "")
    print(f"=== summary: {sent} succeeded, {failed} failed, result={result} ===")

    if failed and fail_on_error:
        print(f"::error::{failed} target(s) failed and fail_on_error is enabled")
        return 1
    return 0


def main() -> int:
    try:
        return run()
    except ActionError as exc:
        print(f"::error::{exc}")
        write_outputs("failure", 0, 0)
        return 1


if __name__ == "__main__":
    sys.exit(main())
