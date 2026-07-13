# codex_subscription_client

状态：第一版可复用模块，等待真实账号连通性验证

这是一个独立 Python 包，用于模拟 Codex CLI 的网页登录流程，通过用户自己的
ChatGPT/Codex 订阅访问 Codex backend。它不依赖 OpenCode、OpenClaw 或 Codex CLI。

## 能力

- OAuth Authorization Code + PKCE 网页登录。
- 本地 callback server 接收授权码并校验 state。
- access token / refresh token 本地存储与自动刷新。
- token 文件原子写入并限制为当前用户可读写。
- 直接调用 Codex responses backend。
- 同时提供简单文本调用和结构化 tool calling 响应。
- 提供 `login`、`status`、`logout`、`ask` 命令。

## 安装

在任意 Python 3.10+ 虚拟环境中执行：

```bash
python3 -m pip install -e codex_subscription_client
```

## 命令行使用

首次登录：

```bash
codex-subscription login
```

查看登录状态：

```bash
codex-subscription status
```

调用模型：

```bash
codex-subscription ask "只回答 OK" --model gpt-5.4
```

退出并删除本模块保存的 token：

```bash
codex-subscription logout
```

## Python 使用

```python
from codex_subscription import CodexSubscriptionClient

client = CodexSubscriptionClient(model="gpt-5.4")
answer = client.generate("只回答 OK")
print(answer)
```

首次调用时，如果没有可用 token，会自动打开浏览器登录。默认 token 文件为：

```text
~/.codex_subscription/auth.json
```

可以通过 `CODEX_SUBSCRIPTION_TOKEN_FILE` 覆盖保存路径。

## 结构化调用

```python
from codex_subscription import CodexSubscriptionClient

client = CodexSubscriptionClient(model="gpt-5.4")
response = client.create_response(
    input_items=[
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "现在几点？"}],
        }
    ],
    tools=[
        {
            "type": "function",
            "name": "local_time",
            "description": "Get local time.",
            "parameters": {"type": "object", "properties": {}},
        }
    ],
)

for call in response.tool_calls:
    print(call.name, call.arguments)
```

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `CODEX_SUBSCRIPTION_MODEL` | 默认模型，默认 `gpt-5.4`。 |
| `CODEX_SUBSCRIPTION_TOKEN_FILE` | token 文件路径。 |
| `CODEX_SUBSCRIPTION_TIMEOUT_SECONDS` | 模型请求超时，默认 180 秒。 |
| `CODEX_SUBSCRIPTION_AUTO_LOGIN` | 设为 `0` 时禁止缺少 token 时自动登录。 |
| `CODEX_SUBSCRIPTION_OPEN_BROWSER` | 设为 `0` 时不自动打开浏览器。 |

## 边界与风险

这个模块面向个人学习和个人订阅使用。它复刻的是 Codex OAuth 客户端行为，调用的是
ChatGPT Codex backend，而不是稳定承诺给第三方应用的 OpenAI Platform API。
OpenAI 可能调整 OAuth client、header、请求结构、模型名或 backend 路径。

不要把它用于多用户服务、转售、共享账号或高并发生产系统。生产应用应使用
OpenAI Platform API，并遵守 OpenAI 的服务条款和使用政策。

## 下一步

1. 使用真实账号完成一次 `login` 和 `ask` 验收。
2. 根据真实 SSE 返回补充兼容性测试。
3. 增加 device-code 登录，支持无本地浏览器环境。
