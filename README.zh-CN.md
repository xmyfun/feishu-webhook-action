# feishu-webhook-action

English · [简体中文](README.zh-CN.md)

通用飞书自定义机器人 GitHub Action：一条通知推到一个或多个群机器人，支持每路独立加签、简单卡片与原始卡片 JSON 透传，并自动附带 GitHub 运行上下文。

- **多路 webhook**：一个 Secret 多行配置，每行一个机器人，逐路发送。
- **每路独立加签**：每行 `url|secret`，按飞书官方规则计算 HMAC-SHA256 签名。
- **两种组包模式**：简单模式只给标题和正文，action 自动套卡片骨架；透传模式直接发完整 card JSON。
- **自动上下文**：默认追加仓库、分支/标签、提交、触发事件、运行链接。
- **零依赖**：Python 3 标准库实现，无第三方包、无构建产物。
- **通知不阻塞 CI**：单路失败只打日志，默认不让 action 失败。

## 为什么用 Python 实现

本 action 的执行逻辑只有四件事：解析 webhook 目标、拼装消息体、计算签名、发 HTTPS 请求。选型时对比了 Node/TypeScript、Go，最终选用 Python 3 标准库，理由如下：

1. **runner 自带解释器，无需 setup**。ubuntu-latest / macos-latest / windows-latest 都预装 `python3`，action 步骤里不需要 `setup-node`、不需要装语言工具链。
2. **标准库覆盖全部需求，零第三方依赖**。加签用 `hmac` / `hashlib` / `base64`，组包用 `json`，发请求用 `urllib.request`，没有 `requirements.txt`，也就不存在依赖供应链投毒的面。
3. **无编译步骤、无 dist 产物，源码可直接审阅**。action 目录里 `notify.py` 就是全部执行逻辑；对比 TS action 需要审阅 `dist/` 压缩编译产物、Go action 需要下载预编译二进制，Python 单文件打开即可读完。
4. **跨平台行为一致**。标准库在三个平台 runner 上表现相同，没有原生依赖、没有 native 编译差异。
5. **用不到 Actions Toolkit**。本 action 不做日志封装、缓存、产物上传等能力，引入 Node 工具链没有实际收益；薄逻辑用标准库表达最直接。

## 快速开始

### 1. 简单模式：Markdown 卡片（CI 通知常用）

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    title: "❌ YMK CI · backend"
    status: failure
    content: |
      **失败 job**：test
      **分支**：${{ github.ref_name }}
  env:
    # webhook 目标也可以走单路回退：
    # FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK }}
    # FEISHU_WEBHOOK_SECRET: ${{ secrets.FEISHU_WEBHOOK_SECRET }}
```

`status` 自动映射卡片颜色：`success` → green，`failure` → red，`cancelled` / `skipped` → grey。也可以用 `template` 直接指定颜色覆盖。

### 2. 纯文本消息

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhook_url: ${{ secrets.FEISHU_WEBHOOK }}
    msg_type: text
    content: "构建完成：${{ github.repository }}@${{ github.ref_name }}"
```

### 3. 透传模式：完整卡片 JSON

需要按钮、多段排版、自定义颜色时，自己拼完整 `card` 对象，action 只负责签名和发送：

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    card_json: |
      {
        "header": {
          "title": {"tag": "plain_text", "content": "发布完成"},
          "template": "green"
        },
        "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": "**版本**：0.1.20"}},
          {"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "打开 Release"},
            "url": "https://github.com/${{ github.repository }}/releases",
            "type": "primary"
          }]}
        ]
      }
```

透传模式下 `title` / `content` / `msg_type` / `extra_elements` / `include_context` 均不生效。

### 4. 多路 webhook 与签名

Secret 里每行一个目标：

```text
https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
https://open.feishu.cn/open-apis/bot/v2/hook/yyyyyyyy|这一路的加签secret
```

未开签名校验的机器人直接填 URL 即可；开了签名校验的那一路在 `|` 后附 secret。

### 5. 调试：dry run

```yaml
- uses: xmyfun/feishu-webhook-action@v0.1.0
  with:
    webhook_url: ${{ secrets.FEISHU_WEBHOOK }}
    content: "测试消息"
    dry_run: "true"
```

只在日志里打印请求体，不实际发送。

## 输入参数

| 输入 | 必需 | 默认 | 说明 |
| --- | --- | --- | --- |
| `webhooks` | 否 | — | 多路目标，每行 `url` 或 `url\|secret`；优先于 `webhook_url` |
| `webhook_url` | 否 | — | 单路 webhook 地址，`webhooks` 为空时回退 |
| `webhook_secret` | 否 | — | 单路签名密钥，配合 `webhook_url` |
| `msg_type` | 否 | `markdown` | 简单模式：`text` / `markdown` |
| `title` | 否 | — | 卡片标题 |
| `content` | 否 | — | 简单模式正文（纯文本或 markdown） |
| `card_json` | 否 | — | 透传模式完整 card 对象 JSON |
| `status` | 否 | — | `success` / `failure` / `cancelled` / `skipped`，映射卡片颜色 |
| `template` | 否 | — | 直接指定卡片颜色，覆盖 `status` 映射 |
| `include_context` | 否 | `true` | 简单模式是否自动追加 GitHub 上下文 |
| `extra_elements` | 否 | — | 追加到卡片 elements 的 JSON 数组（按钮等） |
| `dry_run` | 否 | `false` | 只打印请求体，不发送 |
| `fail_on_error` | 否 | `false` | 任一路失败时 action 是否退出非零 |

## 输出

| 输出 | 说明 |
| --- | --- |
| `status` | `success` / `failure` / `skipped`（未配置 webhook） |
| `sent` | 发送成功路数 |
| `failed` | 发送失败路数 |

## 消息类型与路线图

当前支持：`text` 纯文本、`interactive` 消息卡片（简单 markdown 组包或完整 JSON 透传）。

规划中（待后续版本补充）：

- `post` 富文本：段落级文字、超链接、@人、图片混排。
- `image` 消息：凭 `image_key` 发送图片。
- `share_chat` 群名片。
- 消息卡片 schema 2.0 结构适配。
- 发送失败的可选重试与退避。

## 本地开发与测试

```bash
# 语法检查
python3 -m py_compile notify.py

# dry run：不发请求，打印组包结果
INPUT_WEBHOOK_URL="https://example.invalid/hook" \
INPUT_TITLE="本地测试" \
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

仓库内 `.github/workflows/test.yml` 提供 `workflow_dispatch` 触发的 dry-run 自测，不会向真实机器人发消息。

## 许可证

[MIT](LICENSE)
