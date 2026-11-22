# feishu-webhook-action

[简体中文](README.zh-CN.md) · English

Generic Feishu (Lark) custom-bot GitHub Action: push one notification to one or more group bots, with per-target signing, simple cards and raw card JSON passthrough, and an optional GitHub run context appended.

- **Multi-target webhooks**: configure one secret with one bot per line; each target is sent separately.
- **Per-target signing**: each line is `url|secret`; the HMAC-SHA256 signature follows the official Feishu spec.
- **Two assembly modes**: simple mode takes a title and body and the action wraps the card skeleton; passthrough mode sends a complete card JSON as-is.
- **Automatic context**: repository, ref, commit, triggering event and run link are appended by default.
- **Zero dependencies**: implemented with the Python 3 standard library; no third-party packages, no build artifacts.
- **Non-blocking notifications**: a single target failure only logs; the action does not fail by default.

## Why Python

This action only does four things: parse webhook targets, assemble the message body, compute the signature, and issue the HTTPS request. Node/TypeScript and Go were considered; Python 3 standard library was chosen for these reasons:

1. **Runner ships the interpreter, no setup needed.** `ubuntu-latest` / `macos-latest` / `windows-latest` all preinstall `python3`; no `setup-node` or language toolchain install is required in the action step.
2. **The standard library covers every need, zero third-party dependencies.** Signing uses `hmac` / `hashlib` / `base64`, body assembly uses `json`, requests use `urllib.request`; there is no `requirements.txt`, so there is no dependency supply-chain attack surface.
3. **No compile step, no dist artifact, source is reviewable.** `notify.py` in this directory is the entire execution logic; in contrast, a TS action requires reviewing a compiled `dist/` bundle and a Go action requires downloading a prebuilt binary. The Python single file can be read end to end.
4. **Consistent behavior across platforms.** The standard library behaves the same on all three runner images; there are no native dependencies or native build differences.
5. **The Actions Toolkit is not needed.** This action does not wrap logging, caching, artifact uploads or similar capabilities; pulling in the Node toolchain yields no real benefit, and thin logic is expressed most directly with the standard library.

## Quick start

### 1. Simple mode: Markdown card (common for CI notifications)

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    title: "❌ YMK CI · backend"
    status: failure
    content: |
      **Failed job**: test
      **Ref**: ${{ github.ref_name }}
  env:
    # A single-target fallback is also supported:
    # FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK }}
    # FEISHU_WEBHOOK_SECRET: ${{ secrets.FEISHU_WEBHOOK_SECRET }}
```

`status` maps the card color automatically: `success` → green, `failure` → red, `cancelled` / `skipped` → grey. Use `template` to override the color directly.

### 2. Plain text message

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhook_url: ${{ secrets.FEISHU_WEBHOOK }}
    msg_type: text
    content: "Build finished: ${{ github.repository }}@${{ github.ref_name }}"
```

### 3. Passthrough mode: complete card JSON

When you need buttons, multi-section layout or a custom color, assemble the complete `card` object yourself; the action only handles signing and transport:

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    card_json: |
      {
        "header": {
          "title": {"tag": "plain_text", "content": "Release finished"},
          "template": "green"
        },
        "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": "**Version**: 0.1.20"}},
          {"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "Open Release"},
            "url": "https://github.com/${{ github.repository }}/releases",
            "type": "primary"
          }]}
        ]
      }
```

In passthrough mode, `title` / `content` / `msg_type` / `extra_elements` / `include_context` have no effect.

### 4. Multi-target webhooks and signing

Each target occupies one line in the secret:

```text
https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
https://open.feishu.cn/open-apis/bot/v2/hook/yyyyyyyy|per-target signing secret
```

Bots without signature verification only need the URL; for a bot with signature verification, append its secret after `|`.

### 5. Debug: dry run

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhook_url: ${{ secrets.FEISHU_WEBHOOK }}
    content: "Test message"
    dry_run: "true"
```

The request body is printed to the logs only; nothing is actually sent.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `webhooks` | no | — | Multi-line targets, one `url` or `url\|secret` per line; takes precedence over `webhook_url` |
| `webhook_url` | no | — | Single webhook URL, fallback when `webhooks` is empty |
| `webhook_secret` | no | — | Single-target signing secret, paired with `webhook_url` |
| `msg_type` | no | `markdown` | Simple mode: `text` / `markdown` |
| `title` | no | — | Card title |
| `content` | no | — | Simple-mode body (plain text or markdown) |
| `card_json` | no | — | Passthrough mode: a complete card object as JSON |
| `status` | no | — | `success` / `failure` / `cancelled` / `skipped`, mapped to the card color |
| `template` | no | — | Card color directly, overriding the `status` mapping |
| `include_context` | no | `true` | Whether the GitHub context is appended automatically in simple mode |
| `extra_elements` | no | — | JSON array appended to the card elements (buttons, etc.) |
| `dry_run` | no | `false` | Print the request body only, do not send |
| `fail_on_error` | no | `false` | Whether the action exits non-zero when any target fails |

## Outputs

| Output | Description |
| --- | --- |
| `status` | `success` / `failure` / `skipped` (no webhook configured) |
| `sent` | Number of targets sent successfully |
| `failed` | Number of targets that failed |

## Message types and roadmap

Currently supported: `text` plain text, `interactive` card (simple markdown assembly or complete JSON passthrough).

Planned for later releases:

- `post` rich text: paragraph-level text, hyperlinks, mentions, inline image layout.
- `image` message: send an image by `image_key`.
- `share_chat` group card.
- Message card schema 2.0 structure support.
- Optional retry and backoff on send failure.

## Local development and testing

```bash
# Syntax check
python3 -m py_compile notify.py

# Dry run: no request is issued; the assembled body is printed
INPUT_WEBHOOK_URL="https://example.invalid/hook" \
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

The `.github/workflows/test.yml` workflow in this repository provides a `workflow_dispatch` dry-run self-check; it does not send messages to any real bot.

## License

[MIT](LICENSE)
