# feishu-webhook-action

[English](README.md) · 简体中文

[![Marketplace](https://img.shields.io/badge/GitHub_Marketplace-Feishu_Webhook-blue)](https://github.com/marketplace/actions/feishu-webhook) [![tests](https://img.shields.io/github/actions/workflow/status/xmyfun/feishu-webhook-action/test.yml?branch=main&label=tests)](https://github.com/xmyfun/feishu-webhook-action/actions/workflows/test.yml) [![Release](https://img.shields.io/github/v/release/xmyfun/feishu-webhook-action)](https://github.com/xmyfun/feishu-webhook-action/releases) [![License](https://img.shields.io/github/license/xmyfun/feishu-webhook-action)](LICENSE)

通用飞书自定义机器人 GitHub Action：一条通知推到一个或多个群机器人，支持每路独立加签、纯文本或消息卡片，并可选附带 GitHub 运行上下文。

- **多路 webhook**：一个 Secret 多行配置，每行一个机器人，逐路发送。
- **加签**：全局默认密钥 + 每行 `url|secret` 覆盖，算法与飞书官方一致。
- **两种消息类型**：`msg_type: interactive`（卡片，可给标题+正文自动套骨架，也可给完整 `card` JSON）与 `msg_type: text`（纯文本）。
- **自动上下文**：默认追加仓库、分支/标签、提交、触发事件、运行链接。
- **零依赖**：Python 3 标准库实现，无第三方包、无构建产物。
- **通知不阻塞 CI**：默认单路失败只打 `::warning::`，不让 step 失败；设 `fail_on_error: true` 才阻塞。
- **接口自 v1 冻结**：下面「输入参数」「输出」「行为契约」三节是 v1 的稳定面，新增只做加法，不改名、不改语义。

## 为什么用 Python 实现

本 action 的执行逻辑只有四件事：解析 webhook 目标、拼装消息体、计算签名、发 HTTPS 请求。选型时对比了 Node/TypeScript、Go，最终选用 Python 3 标准库，理由如下：

1. **runner 自带解释器，无需 setup**。`ubuntu-latest` / `macos-latest` / `windows-latest` 都预装 `python3`（CI 用三平台 dry-run 每个提交验证这条）。
2. **标准库覆盖全部需求，零第三方依赖**。加签用 `hmac` / `hashlib` / `base64`，组包用 `json`，发请求用 `urllib.request`，没有 `requirements.txt`，也就不存在依赖供应链投毒的面。
3. **无编译步骤、无 dist 产物，源码可直接审阅**。`notify.py` 就是全部执行逻辑。
4. **跨平台行为一致**。标准库在三个平台 runner 上表现相同，没有原生依赖。
5. **用不到 Actions Toolkit**。本 action 不做日志封装、缓存、产物上传，引入 Node 工具链没有实际收益。

## 快速开始

### 1. 卡片（CI 通知常用）

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    webhook_secret: ${{ secrets.FEISHU_WEBHOOK_SECRET }}  # 可选，作为所有目标的默认签名密钥
    title: "❌ YMK CI · backend"
    status: failure
    content: |
      **失败 job**：test
      **分支**：${{ github.ref_name }}
```

`status` 自动映射卡片颜色：`success` → green，`failure` → red，`cancelled` / `skipped` → grey；需要别的颜色用 `template` 覆盖（`blue` / `wathet` / `turquoise` / `green` / `yellow` / `orange` / `red` / `carmine` / `violet` / `purple` / `indigo` / `grey`）。

### 2. 纯文本

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhook: ${{ secrets.FEISHU_WEBHOOK }}
    msg_type: text
    content: "构建完成：${{ github.repository }}@${{ github.ref_name }}"
```

`text` 模式下 `title` 不生效（契约如此），正文是纯文本，不解析 markdown。

### 3. 完整卡片透传

需要按钮、多段排版、自定义颜色时，直接给完整 `card` 对象，action 只负责签名与发送：

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhooks: ${{ secrets.FEISHU_WEBHOOKS }}
    card: |
      {
        "header": {
          "title": {"tag": "plain_text", "content": "发布完成"},
          "template": "green"
        },
        "elements": [
          {"tag": "div", "text": {"tag": "lark_md", "content": "**版本**：1.0.0"}},
          {"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "打开 Release"},
            "url": "https://github.com/${{ github.repository }}/releases",
            "type": "primary"
          }]}
        ]
      }
    include_context: "false"
```

给 `card` 时 `title` / `template` / `extra_elements` 必须留空，同时给会直接报错（不静默忽略）；`include_context` 被忽略并提示。

### 4. 多路 webhook 与签名

Secret 里每行一个目标：

```text
https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
https://open.feishu.cn/open-apis/bot/v2/hook/yyyyyyyy|这一路的加签secret
```

`webhook_secret` 是所有目标的默认密钥；某行写了 `|secret` 就覆盖它。竖线 `|` 不可出现在 URL 或 secret 里。

### 5. 调试：dry run

```yaml
- uses: xmyfun/feishu-webhook-action@v1
  with:
    webhook: ${{ secrets.FEISHU_WEBHOOK }}
    content: "测试消息"
    dry_run: "true"
```

只在日志里打印请求体，不实际发送；请求体同时写到 `body` 输出，便于在 CI 里断言。

## 输入参数（v1 冻结）

| 输入 | 必需 | 默认 | 说明 |
| --- | --- | --- | --- |
| `webhooks` | 否 | — | 多路目标，每行 `url` 或 `url\|secret`；非空时覆盖 `webhook` |
| `webhook` | 否 | — | 单路 webhook 地址，`webhooks` 为空时回退 |
| `webhook_secret` | 否 | — | 全局默认签名密钥，逐行 `url\|secret` 覆盖它 |
| `msg_type` | 否 | `interactive` | `interactive`（卡片）/ `text`（纯文本）；`post` 不支持 |
| `title` | 否 | — | 卡片 `header.title`；`text` 模式与给 `card` 时不生效 |
| `content` | 否 | — | `interactive` → 卡片首个 `lark_md` 段；`text` → `content.text` |
| `card` | 否 | — | 完整卡片对象 JSON（仅 `interactive`，且不能与 `title`/`template`/`extra_elements` 同用） |
| `template` | 否 | 按 `status` | 卡片颜色（`header.template`），非法值降级 `blue` 并告警 |
| `status` | 否 | — | `success` / `failure` / `cancelled` / `skipped`，映射卡片颜色 |
| `include_context` | 否 | `true` | 是否追加 GitHub 运行上下文；给 `card` 时忽略 |
| `extra_elements` | 否 | — | 追加到卡片 elements 末尾的 JSON 数组（按钮等） |
| `timeout` | 否 | `15` | 单次请求超时（秒） |
| `retries` | 否 | `0` | 网络错误 / 429 / 5xx 的额外重试次数（退避 1s、2s…） |
| `dry_run` | 否 | `false` | 只打印请求体不发送，`body` 输出请求体，`sent` 为 0 |
| `fail_on_error` | 否 | `false` | 任一路失败时 step 是否失败 |

## 输出（v1 冻结）

| 输出 | 说明 |
| --- | --- |
| `result` | `success`（全部发送成功，或 dry_run / 未配置 webhook）/ `failure`（至少一路失败） |
| `sent` | 发送成功路数（dry_run 与未配置 webhook 时为 0） |
| `failed` | 发送失败路数 |
| `body` | 仅 `dry_run=true` 时输出请求体 JSON |

## 行为契约（v1 冻结）

1. 未配置任何 webhook（`webhooks` 与 `webhook` 都空）→ `result=skipped`，step 退出 0。
2. 单路失败不影响 step，除非 `fail_on_error=true`；失败一律以 `::warning::` 出现在日志里。
3. 输入组合非法（`msg_type` 不在支持列表、`card` 与 `title`/`template`/`extra_elements` 同时给、`card` JSON 非法等）→ 立即 `::error::` 并失败，不做静默降级。
4. 卡片 element 顺序固定为 `[content, context, extra_elements...]`。
5. 请求体超过 20000 字节：自动组包时截断最长的 `lark_md` 段并告警；完整 `card` 透传时直接报错。
6. 标题超过 40 字符时截断并告警。
7. 签名：`sign = base64(HMAC-SHA256(key = timestamp + "\n" + secret, msg = ""))`，`timestamp` / `sign` 放请求体，不放 URL；单测里有固定向量锁死这条。

## 错误码速查

| code | 含义 | 排查方向 |
| --- | --- | --- |
| `19001` | 参数非法 | 看响应 `msg`；常见是 `card` 结构缺 `header`/`elements` 或 `msg_type` 拼错 |
| `19021` | 签名校验失败 | 密钥是否对；`timestamp` 与本机时间是否偏差 1 小时以上；签名公式是否把 key/message 弄反 |
| `19024` | 关键词未命中 | 机器人开了「自定义关键词」，正文里必须出现该词 |
| `9499` | 触发限流 | 单群机器人限频，降低频率或拆多个机器人 |

## 支持范围与不做的事

支持：`msg_type: text`、`msg_type: interactive`（自动组包或完整 `card` 透传）。

明确不支持（写进 v1 契约，避免误用）：

- `post` 富文本、`image` 消息、`share_chat`——需要另一种消息体结构，且社区用途少。
- `cardkit` 卡片实体——要应用凭证，不走自定义机器人 webhook。
- 消息卡片 schema 2.0 专属字段——按当前 2.0 结构透传 `card` 即可，无需单独适配。

## 本地开发与测试

```bash
# 单元测试（标准库 unittest，零依赖）
python3 -m unittest discover -s tests -v

# 字节编译检查
python3 -m compileall -q notify.py tests

# dry run：不发请求，打印组包结果
INPUT_WEBHOOK="https://example.invalid/hook" \
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

`.github/workflows/test.yml` 在每次 push / PR 跑单测（Python 3.10 与 3.14），并用 `ubuntu` / `macos` / `windows` 三平台矩阵跑本 action 的 dry-run，不会向真实机器人发消息。

## 版本

接口在 `v1.0.0` 冻结：`v1` 是浮动标签（自动收 v1.x 的补丁与新输入），`v1.0.0` 这类精确标签用于锁死行为。输入与输出的改名、语义变更只会发生在 `v2`。

## 许可证

[MIT](LICENSE)
