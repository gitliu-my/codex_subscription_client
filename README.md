# codex_subscription_client

[![CI](https://github.com/gitliu-my/codex_subscription_client/actions/workflows/ci.yml/badge.svg)](https://github.com/gitliu-my/codex_subscription_client/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

将用户自己的 ChatGPT/Codex 订阅封装为 Python SDK 和仅监听本机的
OpenAI-compatible API，并提供命令行工具与 macOS 管理 App。

> [!IMPORTANT]
> 这是非官方实验项目，与 OpenAI 无隶属或背书关系。它使用 Codex 客户端采用的
> OAuth 和后端协议；这些接口不是稳定的第三方公共 API，可能随时变化或停止工作。
> 使用前请自行确认账号、订阅和适用条款允许你的使用方式。不要共享 token、转售
> 账号或用它绕过订阅限制。

当前版本：`0.4.0`（alpha）。项目不依赖本机 Codex CLI，也不经过模型中间商。

## 功能

- OAuth Authorization Code + PKCE 网页登录。
- 本地保存 access/refresh token，并在过期时刷新。
- 实时查询当前账号和客户端 profile 可用的订阅模型。
- 文本、图片和结构化 function calling。
- Python SDK。
- 本地 `/v1/models`、`/v1/responses` 和 `/v1/chat/completions`。
- 本地管理 UI：登录、模型选择、服务启停和真实调用测试。
- macOS 独立终端程序与 App，使用时不需要 Python 或虚拟环境。

## 快速开始

### 从源码运行

```bash
git clone https://github.com/gitliu-my/codex_subscription_client.git
cd codex_subscription_client
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

`-e` 是 editable install，修改源码后不需要重复安装。

登录并查看当前订阅实际开放的模型：

```bash
codex-subscription login
codex-subscription status
codex-subscription models
```

发送文本或图片：

```bash
codex-subscription ask "只回答 OK" \
  --model gpt-5.6-luna \
  --reasoning-effort medium \
  --show-meta

codex-subscription ask "描述这张图片" \
  --image /absolute/path/to/image.png \
  --model gpt-5.6-luna
```

`--show-meta` 显示请求模型、后端返回模型、推理档位和 response ID。模型自己在
自然语言回答中声称的型号不可靠，应以响应元数据为准。

### macOS 独立程序

```bash
./scripts/build_macos.sh
./scripts/install_macos.sh
```

安装后可使用：

- `~/.local/bin/codex-subscription`
- `~/Applications/Codex Subscription.app`

构建脚本在隔离的 `.build-venv` 中使用 PyInstaller。`dist` 里的产物包含 Python
运行时，日常使用无需激活虚拟环境。若系统尚未接受 Xcode 许可，先运行：

```bash
sudo xcodebuild -license accept
```

也可以直接从终端打开管理页：

```bash
codex-subscription ui
```

管理页默认位于 `http://127.0.0.1:8320`。配置保存到
`~/.codex_subscription/settings.json`，OAuth token 保存到
`~/.codex_subscription/auth.json`，两者权限均限制为当前用户。

## 本地 API

启动服务时必须使用 Bearer Key。省略 `--api-key` 时会随机生成并打印一个：

```bash
codex-subscription serve \
  --host 127.0.0.1 \
  --port 8317 \
  --model gpt-5.6-luna \
  --reasoning-effort low
```

需要给浏览器插件配置稳定 Key 时，推荐从管理页复制；也可以显式传入：

```bash
codex-subscription serve --api-key 'replace-with-a-long-random-local-key'
```

Responses API：

```bash
curl http://127.0.0.1:8317/v1/responses \
  -H 'Authorization: Bearer <local-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-luna","input":"只回答 OK"}'
```

Chat Completions API：

```bash
curl http://127.0.0.1:8317/v1/chat/completions \
  -H 'Authorization: Bearer <local-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"gpt-5.6-luna",
    "messages":[{"role":"user","content":"只回答 OK"}]
  }'
```

浏览器扩展来源默认允许 CORS，普通网页来源默认拒绝。确实需要额外网页来源时，可通过
`CODEX_SUBSCRIPTION_ALLOWED_ORIGINS` 传入逗号分隔的完整 Origin；只应添加你信任的
来源。

## Python SDK

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
| `CODEX_SUBSCRIPTION_MODEL` | 默认模型，当前默认 `gpt-5.6-luna`。 |
| `CODEX_SUBSCRIPTION_REASONING_EFFORT` | 默认推理档，默认 `medium`。 |
| `CODEX_SUBSCRIPTION_TOKEN_FILE` | token 文件，默认 `~/.codex_subscription/auth.json`。 |
| `CODEX_SUBSCRIPTION_TIMEOUT_SECONDS` | 模型请求超时，默认 180 秒。 |
| `CODEX_SUBSCRIPTION_AUTO_LOGIN` | 设为 `0` 时禁止自动打开登录。 |
| `CODEX_SUBSCRIPTION_OPEN_BROWSER` | 设为 `0` 时只打印授权地址。 |
| `CODEX_SUBSCRIPTION_API_KEY` | `serve` 使用的本地 Bearer Key。 |
| `CODEX_SUBSCRIPTION_ALLOWED_ORIGINS` | 额外允许的网页 CORS Origin。 |

## 安全说明

- API 和管理页只允许监听 `127.0.0.1`/`localhost`；不要通过反向代理公开到网络。
- API 始终要求随机或显式 Bearer Key，`/health` 仅返回最小健康状态。
- 管理页使用随机会话 Cookie、同源检查和 CSRF 请求头，不返回账号 ID 或 token 路径。
- token 与本地 API Key 仍是磁盘上的敏感凭据。不要提交、分享或粘贴到 Issue。
- `0.4.0` 会自动替换旧版固定的 `codex-local-translate` Key。升级后请从管理页重新复制
  Key 到浏览器翻译插件。

详见 [安全模型](docs/SECURITY_MODEL.md) 和 [安全报告策略](SECURITY.md)。

## 当前边界

- OpenAI-compatible API 只实现常用子集，不是完整 OpenAI Platform API。
- `stream=true` 会返回 SSE，但上游结果仍先在组件内聚合，不是逐 token 转发。
- 暂未提供 Anthropic `/v1/messages`，不能直接替代 CLIProxyAPI 驱动 Claude Code。
- 模型名称、可用推理档位和多模态能力由后端实时返回，不能保证长期不变。
- 当前 macOS App 未签名和公证，适合本机源码构建，不是正式发行包。

退出登录并删除本项目保存的 token：

```bash
codex-subscription logout
```

## 参与贡献

请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。提交安全问题请遵循
[SECURITY.md](SECURITY.md)，不要创建包含凭据的公开 Issue。

## License

[MIT](LICENSE)
