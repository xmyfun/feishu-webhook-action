# feishu-webhook-action

English · [简体中文](README.zh-CN.md)

Generic Feishu (Lark) custom-bot GitHub Action: push one notification to one or more group bots, with per-target signing, plain text or interactive cards, and an optional GitHub run context.

- **Multi-target webhooks**: one secret, one bot per line; each target is sent separately.
- **Signing**: a global default secret plus per-line `url|secret` overrides; the algorithm follows the official Feishu spec.
- **Two message types**: `msg_type: interactive` (a card, either assembled from a title and body or passed through as a complete `card` object) and `msg_type: text`.
- **Automatic context**: repository, ref, commit, triggering event and run link, appended by default.
- **Zero dependencies**: Python 3 standard library; no third-party packages, no build artifacts.
- **Non-blocking by default**: a failed target is reported as `::warning::` and does not fail the step unless `fail_on_error: true`.
- **Interface frozen at v1**: the “Inputs”, “Outputs” and “Behaviour contract” sections below are the stable surface. Additions are allowed; renames and semantic changes are not.

## Why Python

This action only does four things: parse webhook targets, assemble the message body, compute the signature and issue the HTTPS request. Node/TypeScript and Go were considered; Python 3 standard library was chosen because:

1. **The runners ship the interpreter, no setup needed.** `ubuntu-latest` / `macos-latest` / `windows-latest` all preinstall `python3` — CI verifies this claim with a dry-run matrix on every push.
2. **The standard library covers every need, zero third-party dependencies.** Signing uses `hmac` / `hashlib` / `base64`, body assembly uses `json`, requests use `urllib.request`; no `requirements.txt`, hence no dependency supply-chain surface.
3. **No compile step, no dist artifact, source is reviewable.** `notify.py` is the whole execution logic.
4. **Consistent across platforms.** No native dependencies, no native build differences.
5. **The Actions Toolkit is not needed.** No logging wrapper, caching or artifact upload; pulling in the Node toolchain brings no benefit.

## Quick start

### 1. Card (the usual CI notification)

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    webhook_secret: ${{ secrets.FEISHU_WEBHOOK_SECRET }}  # optional: default secret for every target
    title: "❌ YMK CI · backend"
    status: failure
    content: |
      **Failed job**: test
      **Ref**: ${{ github.ref_name }}
```

`status` maps the card colour: `success` → green, `failure` → red, `cancelled` / `skipped` → grey. Override with `template` (`blue` / `wathet` / `turquoise` / `green` / `yellow` / `orange` / `red` / `carmine` / `violet` / `purple` / `indigo` / `grey`).

### 2. Plain text

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhook: ${{ secrets.FEISHU_WEBHOOK }}
    msg_type: text
    content: "Build finished: ${{ github.repository }}@${{ github.ref_name }}"
```

In `text` mode `title` has no effect (by contract) and the body is not parsed as markdown.

### 3. Complete card passthrough

For buttons, multi-section layouts or custom colours, supply the complete `card` object; the action only signs and sends it:

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    card: |
      {
        "header": {
          "title": {"tag": "plain_text", "content": "Release finished"},
          "template": "green"
        },
        "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": "**Version**: 1.0.0"}},
          {"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "Open Release"},
            "url": "https://github.com/${{ github.repository }}/releases",
            "type": "primary"
          }]}
        ]
      }
    include_context: "false"
```

With `card`, `title` / `template` / `extra_elements` must stay empty — supplying both fails the step instead of silently ignoring them; `include_context` is ignored with a warning.

### 4. Multi-target webhooks and signing

One target per line:

```text
https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
https://open.feishu.cn/open-apis/bot/v2/hook/yyyyyyyy|per-target signing secret
```

`webhook_secret` is the default for every target; a per-line `|secret` overrides it. A `|` may appear in neither the URL nor the secret.

### 5. Debug: dry run

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhook: ${{ secrets.FEISHU_WEBHOOK }}
    content: "Test message"
    dry_run: "true"
```

The request body is printed to the log only; nothing is sent. The same body is exposed as the `body` output for assertions.

## Inputs (frozen at v1)

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `webhooks` | no | — | Multi-line targets, one `url` or `url\|secret` per line; takes precedence over `webhook` |
| `webhook` | no | — | Single webhook URL, fallback when `webhooks` is empty |
| `webhook_secret` | no | — | Global default signing secret; per-line `url\|secret` overrides it |
| `msg_type` | no | `interactive` | `interactive` (card) / `text` (plain text); `post` is not supported |
| `title` | no | — | Card `header.title`; ignored in `text` mode and when `card` is given |
| `content` | no | — | `interactive` → first `lark_md` block of the card; `text` → `content.text` |
| `card` | no | — | Complete card object as JSON (only with `interactive`, and not together with `title` / `template` / `extra_elements`) |
| `template` | no | from `status` | Card colour (`header.template`); invalid values fall back to `blue` with a warning |
| `status` | no | — | `success` / `failure` / `cancelled` / `skipped`, mapped to the card colour |
| `include_context` | no | `true` | Append the GitHub run context; ignored when `card` is given |
| `extra_elements` | no | — | JSON array appended to the card elements (buttons, etc.) |
| `timeout` | no | `15` | HTTP timeout per attempt, seconds |
| `retries` | no | `0` | Extra attempts after a network error, 429 or 5xx (backoff 1s, 2s, …) |
| `dry_run` | no | `false` | Print the body only; `body` output set, `sent` stays 0 |
| `fail_on_error` | no | `false` | Fail the step when any target fails |

## Outputs (frozen at v1)

| Output | Description |
| --- | --- |
| `result` | `success` (all targets sent, or dry_run / no webhook configured) / `failure` (at least one target failed) |
| `sent` | Targets sent successfully (0 in dry_run and when skipped) |
| `failed` | Targets that failed to send |
| `body` | Request body JSON, only when `dry_run=true` |

## Behaviour contract (frozen at v1)

1. No webhook configured (`webhooks` and `webhook` both empty) → `result=skipped`, exit 0.
2. A failed target does not fail the step unless `fail_on_error=true`; failures always surface as `::warning::`.
3. Invalid input combinations (`msg_type` outside the supported list, `card` together with `title`/`template`/`extra_elements`, malformed `card` JSON, …) fail immediately with `::error::` — no silent downgrade.
4. Card element order is fixed: `[content, context, extra_elements...]`.
5. Request bodies over 20000 bytes: assembled cards truncate the longest `lark_md` block with a warning; a complete passed-through `card` fails instead.
6. Titles longer than 40 characters are truncated with a warning.
7. Signature: `sign = base64(HMAC-SHA256(key = timestamp + "\n" + secret, msg = ""))`; `timestamp` / `sign` go into the request body, not the URL. A golden vector in the unit tests locks this down.

## Error codes

| code | Meaning | Look at |
| --- | --- | --- |
| `19001` | Invalid parameters | `msg` in the response; usually a `card` missing `header`/`elements` or a wrong `msg_type` |
| `19021` | Signature mismatch | Wrong secret; machine clock off by more than one hour; key/message swapped in the sign formula |
| `19024` | Keyword not matched | The bot has a custom keyword; the body must contain it |
| `9499` | Rate limited | Per-bot frequency limit; slow down or split across bots |

## Scope and non-goals

Supported: `msg_type: text`, and `msg_type: interactive` (assembled card or complete `card` passthrough).

Explicitly out of scope for v1:

- `post` rich text, `image` messages, `share_chat` — different body shapes and rarely used.
- `cardkit` card entities — they need app credentials and do not go through the custom-bot webhook.
- Schema 2.0-only fields — pass a complete `card` for full control instead.

## Local development and testing

```bash
# Unit tests (standard-library unittest, zero dependencies)
python3 -m unittest discover -s tests -v

# Byte-compile check
python3 -m compileall -q notify.py tests

# Dry run: no request is issued, the assembled body is printed
INPUT_WEBHOOK="https://example.invalid/hook" \
INPUT_TITLE="Local test" \
INPUT_CONTENT="**hello** markdown" \
INPUT_STATUS="failure" \
INPUT_DRY_RUN="true" \
GITHUB_REPOSITORY="xmyfun/feishu-webhook-action" \
GITHUB_REF_NAME="main" \
GITHUB_SHA="deadbeef" \
GITHUB_EVENT_NAME="workflow_dispatch" \
GITHUB_RUN_ID="12345" \
python3 notify.py
```

`.github/workflows/test.yml` runs the unit tests on Python 3.10 and 3.14 plus a dry-run matrix of this action on `ubuntu` / `macos` / `windows` for every push and pull request; it never talks to a real bot.

## Versioning

The interface is frozen at `v1.0.0`: `v1` is a floating tag (picks up v1.x patches and new inputs), while `v1.0.0` pins behaviour exactly. Renames and semantic changes of inputs or outputs only happen in `v2`.

## License

[MIT](LICENSE)
