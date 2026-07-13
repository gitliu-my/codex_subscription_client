# codex_subscription_client

状态：`0.2.0`，已通过真实 ChatGPT OAuth、GPT-5.6 Luna 文本调用验收。

这是一个独立 Python 包，把用户自己的 ChatGPT/Codex 订阅转换成 Python SDK
和本地 OpenAI-compatible API。它不调用或依赖本机 Codex CLI，也不经过模型中间商。

## 已支持

- OAuth Authorization Code + PKCE 网页登录。
- access token / refresh token 安全存储与自动刷新。
- 组件内置 Codex 协议 profile，不读取本机 Codex 版本。
- 实时查询当前 profile 可用的订阅模型。
- 文本、图片和结构化 function calling。
- 临时网络或 TLS 断连自动重试。
- Python SDK。
- 本地 `/v1/models`、`/v1/responses` 和 `/v1/chat/completions`。

## 安装

在项目自己的虚拟环境中安装：

```bash
cd codex_subscription_client
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

`-e` 表示 editable install，修改源码后不需要反复重装。

## 登录和模型

```bash
codex-subscription login
codex-subscription status
codex-subscription models
```

`models` 查询 OpenAI Codex backend 的实时模型列表，不读取本机 Codex 缓存。

## 命令行调用

文本：

```bash
codex-subscription ask "只回答 OK" \
  --model gpt-5.6-luna \
  --reasoning-effort medium \
  --show-meta
```

`--show-meta` 会显示请求模型、后端响应模型、推理档位和 response ID。

图片，可以多次传入 `--image`：

```bash
codex-subscription ask "描述这张图片" \
  --image /absolute/path/to/image.png \
  --model gpt-5.6-luna
```

## 本地 API

启动服务，默认仅监听本机：

```bash
codex-subscription serve \
  --host 127.0.0.1 \
  --port 8317 \
  --model gpt-5.6-luna \
  --reasoning-effort medium
```

Responses API：

```bash
curl http://127.0.0.1:8317/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-luna","input":"只回答 OK"}'
```

Chat Completions API：

```bash
curl http://127.0.0.1:8317/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"gpt-5.6-luna",
    "messages":[{"role":"user","content":"只回答 OK"}]
  }'
```

需要保护本地端口时，启动命令增加 `--api-key local-secret`，调用方发送：

```text
Authorization: Bearer local-secret
```

## Python 使用

```python
from codex_subscription import CodexSubscriptionClient

client = CodexSubscriptionClient(
    model="gpt-5.6-luna",
    reasoning_effort="medium",
)

print(client.generate("只回答 OK"))
print(client.generate("描述图片", images=["/absolute/path/to/image.png"]))

for model in client.list_models():
    print(model.slug, model.input_modalities)
```

底层结构化调用使用 `create_response(input_items, tools, instructions)`，返回
`CodexResponse`，其中包含 `text`、`tool_calls` 和原始 `output_items`。

## 配置

| 变量 | 用途 |
| --- | --- |
| `CODEX_SUBSCRIPTION_MODEL` | 默认模型，默认 `gpt-5.6-luna`。 |
| `CODEX_SUBSCRIPTION_REASONING_EFFORT` | 默认推理档，默认 `medium`。 |
| `CODEX_SUBSCRIPTION_TOKEN_FILE` | token 文件，默认 `~/.codex_subscription/auth.json`。 |
| `CODEX_SUBSCRIPTION_TIMEOUT_SECONDS` | 模型请求超时，默认 180 秒。 |
| `CODEX_SUBSCRIPTION_AUTO_LOGIN` | 设为 `0` 时禁止自动打开登录。 |
| `CODEX_SUBSCRIPTION_OPEN_BROWSER` | 设为 `0` 时只打印授权地址。 |

## 当前边界

- 本地 API 是首版 OpenAI-compatible 子集，不是完整 OpenAI Platform API。
- `stream=true` 当前会返回 SSE 格式，但上游结果仍先在组件内聚合，不是逐 token 转发。
- 暂未提供 Anthropic `/v1/messages`，因此还不能直接替代 CLIProxyAPI 驱动 Claude Code。
- Codex backend 并非承诺稳定的第三方公共 API；协议 profile 和请求字段可能变化。
- 仅用于自己的账号和个人订阅，不共享 token、不转售账号、不绕过订阅用量限制。

退出登录并删除本模块保存的 token：

```bash
codex-subscription logout
```
