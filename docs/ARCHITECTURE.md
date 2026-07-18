# Architecture

```text
CLI / macOS App / Python caller
             |
             v
      codex_subscription
       |      |       |
       |      |       +-- Local dashboard (loopback only)
       |      +---------- OpenAI-compatible API (loopback + Bearer key)
       +----------------- OAuth and subscription client
                              |
                              v
                    OpenAI authentication/backend
```

## Modules

- `auth.py`: PKCE login, callback listener, token persistence, refresh, and status.
- `client.py`: Codex client profile, model discovery, multimodal request conversion, backend
  transport, retry, and SSE response parsing.
- `server.py`: local OpenAI-compatible HTTP facade and browser-extension CORS policy.
- `ui.py`: local dashboard, settings persistence, API process management, and dashboard
  session protection.
- `cli.py`: user-facing `login`, `status`, `models`, `ask`, `serve`, and `ui` commands.

The reusable library is under `src/codex_subscription`. PyInstaller entry points under
`packaging` are intentionally thin so the same modules power source installs and standalone
artifacts.
