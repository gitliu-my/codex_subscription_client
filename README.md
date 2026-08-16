# codex_subscription_client

[![CI](https://github.com/gitliu-my/codex_subscription_client/actions/workflows/ci.yml/badge.svg)](https://github.com/gitliu-my/codex_subscription_client/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

将用户自己的 ChatGPT/Codex 订阅封装为 Python SDK 和仅监听本机的
OpenAI-compatible API，并提供命令行工具与浏览器管理页面。

> [!IMPORTANT]
> 这是非官方实验项目，与 OpenAI 无隶属或背书关系。它使用 Codex 客户端采用的
> OAuth 和后端协议；这些接口不是稳定的第三方公共 API，可能随时变化或停止工作。
> 使用前请自行确认账号、订阅和适用条款允许你的使用方式。不要共享 token、转售
> 账号或用它绕过订阅限制。

当前版本：`0.9.1`（alpha）。项目不依赖本机 Codex CLI，也不经过模型中间商。

## 功能

- OAuth Authorization Code + PKCE 网页登录。
- 本地保存 access/refresh token，并在过期时刷新。
- 实时查询当前账号和客户端 profile 可用的订阅模型。
- 文本、图片和结构化 function calling。
- Python SDK。
- 本地 `/v1/models`、`/v1/responses` 和 `/v1/chat/completions`。
- 每个应用独立 API Key，可从 CLI 或本机管理页创建、查看、复制、重命名、
  配置模型/推理档位白名单、禁用和删除；应用 Key 原始值保存在系统安全存储中。
- 宽屏管理 UI：登录、模型选择、服务启停、图片输入，以及订阅直连/本地 API
  双路径测试。
- 终端方向键配置向导，CLI 与浏览器管理页共用默认模型和推理档位。
- macOS arm64 与 Linux x86_64 独立终端程序，使用时不需要 Python 或虚拟环境。

## 快速开始

### Homebrew 安装

首个发布目标是 Apple Silicon macOS。Homebrew 6 需要显式信任第三方 Tap 中的
Formula：

```bash
brew tap gitliu-my/tap
brew trust --formula gitliu-my/tap/csub
brew install gitliu-my/tap/csub
```

更新并重启后台 API：

```bash
brew update
brew upgrade csub
csub restart
```

发布维护流程见 [docs/RELEASING.md](docs/RELEASING.md)。

### Linux x86_64 安装

Ubuntu 等 x86_64 Linux 服务器可以安装到当前用户的 `~/.local`，不需要 sudo：

```bash
curl -fsSL \
  https://raw.githubusercontent.com/gitliu-my/codex_subscription_client/main/scripts/install_linux.sh \
  | sh
export PATH="$HOME/.local/bin:$PATH"
```

无桌面服务器执行 `csub login` 时会自动使用手动回调模式。打开终端打印的授权地址，
授权结束后即使浏览器显示无法访问 `localhost`，也只需复制地址栏中的完整地址并粘贴
回终端，不需要在服务器开放 OAuth 端口：

```bash
csub login
csub status
csub models
```

从本机安全访问服务器上的管理页，可以建立 SSH 隧道：

```bash
ssh -L 8320:127.0.0.1:8320 user@server
csub ui --no-browser
```

随后在本机浏览器打开 `http://127.0.0.1:8320`。API 端口同样可以使用
`ssh -L 8317:127.0.0.1:8317 user@server` 转发。

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
csub login
csub status
csub models
```

直接运行 `csub` 会正常显示命令帮助，不会报缺少参数。`csub status` 同时显示
登录状态、默认模型、本地 API 地址和服务状态；服务状态可能为 `running`、
`stopped`、`key_mismatch` 或 `port_in_use`。

后台启动、停止或重启本地 API：

```bash
csub start
csub status
csub restart
csub stop
```

UI 和这些命令管理同一个后台 API。关闭浏览器或终端管理页不会
停止 API；服务日志保存在 `~/.codex_subscription/api.log`。如果本机存在执行
`csub serve` 的 `~/Library/LaunchAgents/com.gitliu-my.csub-api.plist`，`start` 和
`stop` 会分别加载和卸载该守护任务，同时保留 plist 文件及其备用机配置。
执行 `csub logout` 会先停止本地 API，再删除 OAuth token。

使用方向键选择并保存默认模型和推理档位：

```bash
csub config
```

用 `↑`/`↓` 或 `j`/`k` 移动，回车确认，`q` 或 Esc 取消。模型列表来自当前
订阅的实时接口，第二步只显示所选模型支持的推理档位。配置保存后，终端
`ask`、`serve` 和浏览器管理页会共用这套默认值。

脚本环境可以跳过交互菜单：

```bash
csub config \
  --model gpt-5.6-luna \
  --reasoning-effort medium
```

发送文本或图片：

```bash
csub ask "只回答 OK" \
  --model gpt-5.6-luna \
  --reasoning-effort medium \
  --show-meta

csub ask "描述这张图片" \
  --image /absolute/path/to/image.png \
  --model gpt-5.6-luna
```

设置过默认值后，日常调用可以简化为：

```bash
csub ask "只回答 OK"
```

`--show-meta` 显示请求模型、后端返回模型、推理档位和 response ID。模型自己在
自然语言回答中声称的型号不可靠，应以响应元数据为准。

为不同应用创建独立 API Key：

```bash
csub keys create browser-translator
csub keys create my-agent
csub keys
csub keys reveal csub_live_ab12cd34
csub keys permissions csub_live_ab12cd34 \
  --allow gpt-5.6-luna=low,medium \
  --allow gpt-5.6-sol=low
```

还可以使用 `rename`、`enable`、`disable` 和 `delete --yes` 管理 Key。升级时，原有
单一 Key 会自动登记为“默认兼容 Key”，已有调用不需要立即修改。新 Key 默认只允许
当前配置的模型和推理档位；`create --unrestricted` 或
`permissions KEY --unrestricted` 可显式开放全部权限。

### 独立 CLI 构建

macOS arm64：

```bash
./scripts/build_macos.sh
./scripts/install_macos.sh
```

Linux x86_64：

```bash
./scripts/build_linux.sh
./scripts/package_linux_release.sh
./scripts/install_linux.sh release/csub-linux-x86_64.tar.gz
```

安装后可使用：

- `~/.local/bin/csub`
- `~/.local/lib/csub/`（独立 CLI 运行时）

构建脚本在隔离的 `.build-venv` 中使用 PyInstaller。CLI 使用免解包的 `onedir`
形式安装，避免单文件程序每次启动时等待数秒；运行时仍随程序提供，日常使用无需
安装 Python 或激活虚拟环境。

也可以直接从终端打开管理页：

```bash
csub ui
```

管理页默认位于 `http://127.0.0.1:8320`。配置保存到
`~/.codex_subscription/settings.json`，OAuth token 保存到
`~/.codex_subscription/auth.json`，API Key 元数据保存到权限为 `0600` 的
`~/.codex_subscription/api_keys.db`。应用 Key 原始值在 macOS 存入 Keychain，在 Linux
存入权限为 `0600` 的 `~/.codex_subscription/secrets/`。管理页将服务默认
配置、单次请求调试和应用 Key 权限分别放在 `API 控制台`、`API 调试台` 与 `API Keys` 三个
视图中；侧栏显示当前 ChatGPT 姓名，悬停或点击可查看邮箱、订阅类型与 Account ID，
不会显示 OAuth token。控制台中的接口地址由本机端口自动生成，只读并可直接复制。调试台支持文本与
最多四张图片，并可分别验证订阅后端、`/v1/responses` 和
`/v1/chat/completions`。实验台的流式开关会实时显示模型增量，并在完成后显示首字
耗时、总耗时、实时/最终输出速率和输入/输出/总 Token；结果页同时保留文本、原始事件
和脱敏后的实际请求。开启“图片生成”后，实验台会通过 Responses 的
`image_generation` 工具按输出顺序展示文字和图片，并提供质量、尺寸与下载控制。
管理页中的 `API Keys` 独立视图可按 Key 配置模型与推理档位白名单，修改后对运行中的
API 服务立即生效。

## 本地 API

启动服务时必须使用 Bearer Key。每个应用应使用 `csub keys create` 生成的独立 Key；
升级前已有的稳定 Key 会作为默认兼容 Key 继续可用。日常使用后台服务：

```bash
csub start
```

需要在当前终端查看服务输出、按 `Ctrl+C` 停止时，使用前台调试模式：

```bash
csub serve \
  --host 127.0.0.1 \
  --port 8317 \
  --model gpt-5.6-luna \
  --reasoning-effort low \
  --max-concurrency 10
```

前台调试时也可以显式覆盖 Key：

```bash
csub serve --api-key 'replace-with-a-long-random-local-key'
```

Responses API：

```bash
curl http://127.0.0.1:8317/v1/responses \
  -H 'Authorization: Bearer <local-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-luna","input":"只回答 OK"}'
```

实时 SSE 输出：

```bash
curl --no-buffer http://127.0.0.1:8317/v1/responses \
  -H 'Authorization: Bearer <local-api-key>' \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-luna","input":"写三句话","stream":true}'
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

API 默认最多同时向订阅后端发送 10 个请求，超过上限的请求最多排队 30 秒，之后返回
`429`。可通过管理页或 `--max-concurrency` 调整。OAuth Token 过期时只会有一个请求
执行刷新，其余并发请求复用刷新后的 Token。

`/v1/responses` 会实时转发上游 SSE，并从最终事件返回 `usage`。请求参数会先经过明确的
Codex backend 方言转换，不再把未知字段直接透传：

| 参数 | csub 行为 |
| --- | --- |
| `model`、`input`、`tools`、`instructions`、`stream` | 本地解析并构造 backend 请求。 |
| `reasoning`（`effort`/`summary`/`context`/`mode`）、`tool_choice`、`parallel_tool_calls`、`text`（`format`/`verbosity`）、`include`、`service_tier`、`prompt_cache_key` | 校验后传给 backend；`reasoning`、`text` 和 `include` 会与 csub 必需值安全合并。 |
| `store` | 仅接受 `false` 或 `null`；csub 始终使用 `store:false`，不提供持久化 Response 状态。 |
| `max_output_tokens` | 为兼容 OpenAI SDK/DSH 接受并忽略；**不会限制或截断输出**。 |
| `prompt_cache_retention`、`prompt_cache_options` | 接受并忽略；当前不会改变 backend 缓存策略。 |
| `stream_options` | 接受并忽略；Responses 最终事件仍会保留 backend 返回的 `usage`。 |
| `temperature`、`top_p`、`previous_response_id`、`conversation`、`background` 等未实现标准参数 | 在本地返回 `400 invalid_request_error`，避免伪装参数已经生效。 |
| 未知参数 | 在本地返回 `400 invalid_request_error`。 |

忽略兼容字段时，HTTP 响应头 `X-Csub-Ignored-Request-Fields` 会列出字段名，流式和
非流式请求一致。真正由 Codex backend 或 OAuth 产生的故障仍返回 `502`。因此 DSH
可以继续按标准 Responses 语义发送由 `maxTokens` 映射出的 `max_output_tokens`，但该值
只是兼容提示，不是 csub 的强制 Token 配额。为了让配置更容易辨识，可把 DSH provider
ID 命名为 `csub` 并显式设置 `api: openai-responses`；名称本身不影响协议行为。

本地客户端断开时，csub 会关闭对应的上游响应连接，但远端是否立即终止生成仍由订阅
后端决定。

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
| `CODEX_SUBSCRIPTION_REASONING_EFFORT` | 默认推理档，默认 `low`。 |
| `CODEX_SUBSCRIPTION_TOKEN_FILE` | token 文件，默认 `~/.codex_subscription/auth.json`。 |
| `CODEX_SUBSCRIPTION_SECRETS_DIR` | Linux 应用 Key 原始值目录，默认 `~/.codex_subscription/secrets`。 |
| `CODEX_SUBSCRIPTION_TIMEOUT_SECONDS` | 模型请求超时，默认 180 秒。 |
| `CODEX_SUBSCRIPTION_AUTO_LOGIN` | 设为 `0` 时禁止自动打开登录。 |
| `CODEX_SUBSCRIPTION_OPEN_BROWSER` | 设为 `0` 时只打印授权地址。 |
| `CODEX_SUBSCRIPTION_API_KEY` | `serve` 使用的本地 Bearer Key。 |
| `CODEX_SUBSCRIPTION_ALLOWED_ORIGINS` | 额外允许的网页 CORS Origin。 |
| `CODEX_SUBSCRIPTION_FORCE_IPV4` | 设为 `1` 时，出站请求仅使用 IPv4；用于绕过不可达的 IPv6 路由。 |

命令行参数优先级最高，其次是环境变量，再其次是
`~/.codex_subscription/settings.json` 中保存的默认值。

## 安全说明

- API 和管理页只允许监听 `127.0.0.1`/`localhost`；不要通过反向代理公开到网络。
- API 始终要求随机或显式 Bearer Key，`/health` 仅返回最小健康状态。
- 管理页使用随机会话 Cookie、同源检查和 CSRF 请求头，不返回账号 ID 或 token 路径。
- 应用 Key 的原始值在 macOS 保存在 Keychain，在 Linux 保存在仅当前用户可读的独立
  密钥文件中；SQLite 只保存不可逆指纹和使用元数据。
  OAuth token 和默认兼容 Key 仍受本机用户权限保护，不要提交、分享或粘贴到 Issue。
- 应用 Key 的模型和推理档位白名单在请求进入订阅后端前校验；越权请求返回 `403`，
  `/v1/models` 只列出该 Key 可访问的模型。
- `0.4.0` 会自动替换旧版固定的 `codex-local-translate` Key。升级后请从管理页重新复制
  Key 到浏览器翻译插件。

详见 [安全模型](docs/SECURITY_MODEL.md) 和 [安全报告策略](SECURITY.md)。

## 当前边界

- OpenAI-compatible API 只实现常用子集，不是完整 OpenAI Platform API。
- `stream=true` 会实时转发文本与工具参数增量；事件片段不保证恰好对应一个字符。
- Responses 只实现上表列出的参数子集；不支持或未知参数会在本地明确返回 `400`。
- 暂未提供 Anthropic `/v1/messages`，不能直接替代 CLIProxyAPI 驱动 Claude Code。
- 暂未提供按 Key 的速率限制和 Token 配额，不能仅依靠它直接暴露公网服务。
- 模型名称、可用推理档位和多模态能力由后端实时返回，不能保证长期不变。

退出登录并删除本项目保存的 token：

```bash
csub logout
```

## 参与贡献

请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。提交安全问题请遵循
[SECURITY.md](SECURITY.md)，不要创建包含凭据的公开 Issue。

## License

[MIT](LICENSE)
