#!/usr/bin/env python3
"""Feishu (Lark) custom-bot webhook sender for GitHub Actions.

Zero-dependency: Python 3 standard library only (hmac, hashlib, base64, json,
urllib, time, os, sys, copy, re). No package install, no build step.

Modes
-----
- Simple card: give ``title`` + ``content`` (markdown); this action assembles the
  interactive card, optionally appends GitHub context and extra elements.
- Raw card: give ``card_json`` (a complete ``card`` object); the action sends it
  as-is, only handling signature and transport.

Webhook targets
---------------
- ``webhooks``: multi-line, one target per line; each line is ``url`` or
  ``url|secret`` (per-target signing secret).
- Fallback: ``webhook_url`` + ``webhook_secret`` for a single target.

One target failing does not fail the action by default; set ``fail_on_error``
to "true" to propagate failures.
"""

import copy
import hashlib
import hmac
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def parse_targets() -> list[tuple[str, str]]:
    """Parse webhook targets from the multi-line input, then single-value fallback."""
    targets: list[tuple[str, str]] = []
    for line in env("INPUT_WEBHOOKS").splitlines():
        line = line.strip()
        if not line:
            continue
        url, _, secret = line.partition("|")
        url, secret = url.strip(), secret.strip()
        if url:
            targets.append((url, secret))
    if not targets:
        url = env("INPUT_WEBHOOK_URL")
        if url:
            targets.append((url, env("INPUT_WEBHOOK_SECRET")))
    return targets


def status_template(status: str) -> str:
    return {
        "success": "green",
        "failure": "red",
        "cancelled": "grey",
        "skipped": "grey",
    }.get(status, "default")


def short_sha(sha: str) -> str:
    return sha[:7] if sha else "?"


def context_markdown() -> str:
    """Assemble GitHub context lines as lark_md; empty when run outside Actions."""
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


def load_extra_elements() -> list:
    raw = env("INPUT_EXTRA_ELEMENTS")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        print("::warning::extra_elements is not an array; ignored")
        return []
    except json.JSONDecodeError as exc:
        print(f"::warning::failed to parse extra_elements JSON; ignored: {exc}")
        return []


def build_simple_body() -> dict:
    msg_type = env("INPUT_MSG_TYPE", "markdown").lower()
    content = env("INPUT_CONTENT")
    title = env("INPUT_TITLE")

    if msg_type == "text":
        text = content
        if env("INPUT_INCLUDE_CONTEXT", "true").lower() not in ("false", "0", "no"):
            ctx = context_markdown()
            if ctx:
                text = f"{text}\n\n{ctx}" if text else ctx
        return {"msg_type": "text", "content": {"text": text}}

    # markdown / interactive → interactive card
    elements: list = []
    if content:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    if env("INPUT_INCLUDE_CONTEXT", "true").lower() not in ("false", "0", "no"):
        ctx = context_markdown()
        if ctx:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": ctx}})
    elements.extend(load_extra_elements())

    template = env("INPUT_TEMPLATE") or status_template(env("INPUT_STATUS"))
    card: dict = {"header": {"template": template}, "elements": elements}
    if title:
        card["header"]["title"] = {"tag": "plain_text", "content": title}
    return {"msg_type": "interactive", "card": card}


def build_raw_body() -> dict | None:
    raw = env("INPUT_CARD_JSON")
    if not raw:
        return None
    try:
        card = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"::error::card_json is not valid JSON: {exc}")
        sys.exit(1)
    if not isinstance(card, dict):
        print("::error::card_json must be an object (a complete card structure)")
        sys.exit(1)
    return {"msg_type": "interactive", "card": card}


def sign(body: dict, secret: str) -> dict:
    """Attach Feishu signature: HMAC-SHA256 of \"timestamp\\nsecret\" over empty
    message, Base64 encoded. Per official custom-bot spec."""
    if not secret:
        return body
    ts = str(int(time.time()))
    digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    signed = copy.deepcopy(body)
    signed["timestamp"] = ts
    signed["sign"] = base64.b64encode(digest).decode()
    return signed


def send(url: str, body: dict, dry_run: bool) -> tuple[bool, str]:
    if dry_run:
        print("=== dry_run request body ===")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return True, "dry_run"
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return False, f"URLError: {exc}"
    except Exception as exc:  # noqa: BLE001 - report any send failure, keep going
        return False, f"{type(exc).__name__}: {exc}"
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return False, f"non-JSON response: {raw[:200]}"
    code = payload.get("code", payload.get("StatusCode", 0))
    if code == 0:
        return True, raw
    return False, f"code={code} msg={payload.get('msg', '')} body={raw[:200]}"


def write_outputs(status: str, sent: int, failed: int) -> None:
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if not gh_output:
        return
    with open(gh_output, "a", encoding="utf-8") as fh:
        fh.write(f"status={status}\nsent={sent}\nfailed={failed}\n")


def main() -> int:
    targets = parse_targets()
    if not targets:
        print("No webhook configured (webhooks / webhook_url are both empty); skipping Feishu notification")
        write_outputs("skipped", 0, 0)
        return 0

    body = build_raw_body() or build_simple_body()
    dry_run = env("INPUT_DRY_RUN", "false").lower() in ("true", "1", "yes")
    fail_on_error = env("INPUT_FAIL_ON_ERROR", "false").lower() in ("true", "1", "yes")

    sent = 0
    failed = 0
    responses: list[str] = []
    for idx, (url, secret) in enumerate(targets):
        signed = sign(body, secret)
        ok, detail = send(url, signed, dry_run)
        if ok:
            sent += 1
            print(f"[{idx}] sent successfully")
        else:
            failed += 1
            print(f"[{idx}] send failed (non-blocking): {detail}")
        responses.append(detail)

    status = "success" if failed == 0 else "failure"
    write_outputs(status, sent, failed)
    print(f"=== summary: {sent} succeeded, {failed} failed ===")

    if failed > 0 and fail_on_error:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
