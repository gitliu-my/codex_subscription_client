# codex_subscription_client

状态：`0.3.0`，已通过真实 ChatGPT OAuth、GPT-5.6 Luna 文本调用验收。

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
- 本地管理 UI，可完成登录、模型选择、API 启停和调用测试。
- macOS 独立终端程序与 App，运行时不需要 Python 或虚拟环境。

## 推荐：独立程序和 App

构建并安装到当前用户目录：

```bash
./scripts/build_macos.sh
./scripts/install_macos.sh
```

macOS 首次使用 Apple 构建工具时，需要先接受一次系统许可：

```bash
sudo xcodebuild -license accept
```

安装后有两个入口：

- 终端命令：`~/.local/bin/codex-subscription`（当前用户的 PATH 已包含该目录）。
- macOS App：`~/Applications/Codex Subscription.app`，可以从 Finder 或 Spotlight 打开。

构建过程会使用隔离的 `.build-venv` 安装 PyInstaller；`dist` 中的最终程序已经
包含 Python 运行时，实际使用不需要激活 `.venv`，也不依赖系统 Python。

打开管理界面也可以直接执行：

```bash
codex-subscription ui
```

管理界面默认位于 `http://127.0.0.1:8320`，可以完成以下操作：

- ChatGPT/Codex 网页登录和退出登录。
- 查询当前订阅实际开放的模型。
- 选择模型与推理档位。
- 启动、停止 OpenAI-compatible API。
- 复制翻译插件需要的接口地址和 API Key。
- 发送一次真实模型测试。

UI 配置保存在 `~/.codex_subscription/settings.json`，文件权限为 `0600`；OAuth
token 仍保存在 `~/.codex_subscription/auth.json`，不会进入构建产物。

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
