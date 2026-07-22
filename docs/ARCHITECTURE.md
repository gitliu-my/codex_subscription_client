# Architecture

```text
CLI / browser dashboard / Python caller
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

- `auth.py`: PKCE login, callback listener, token persistence, single-flight refresh, and
  status.
- `client.py`: Codex client profile, model discovery, multimodal request conversion, retry,
  incremental SSE iteration, response aggregation, and usage parsing.
- `transport.py`: shared HTTPS opener with a packaged CA bundle and `SSL_CERT_FILE` override.
- `server.py`: local OpenAI-compatible HTTP facade, live Responses/Chat SSE forwarding,
  bounded upstream concurrency, and browser-extension CORS policy.
- `service.py`: shared background API lifecycle, authenticated control probes, and detached
  process management for both CLI and dashboard.
- `settings.py`: shared, permission-restricted defaults for CLI and dashboard entry points.
- `terminal_menu.py`: dependency-free arrow-key selector for interactive CLI configuration.
- `ui.py`: responsive dashboard, multimodal direct/API test workbench with incremental text/image
  output, usage metrics, API process management, and dashboard session protection.
- `cli.py`: the `csub` entry point with `login`, `status`, `models`, `config`, `ask`,
  `serve`, and `ui` commands.

The reusable library is under `src/codex_subscription`. PyInstaller entry points under
`packaging` are intentionally thin so the same modules power source installs and standalone
artifacts. The CLI is installed as an `onedir` runtime behind the lightweight `csub` launcher
to avoid per-command extraction overhead.

The dashboard and `csub start/stop/restart` control the same detached API process. Closing the
dashboard only stops its own HTTP server; `csub serve` remains an explicit foreground debugging
mode.

For streaming requests, the handler keeps one upstream HTTPS response open and flushes every SSE
event to the loopback client. Closing the downstream iterator closes that upstream response. The
same bounded semaphore covers streaming, non-streaming, and model-list requests so a long-running
stream retains its slot until completion or cancellation.
