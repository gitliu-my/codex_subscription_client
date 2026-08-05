# Architecture

```text
CLI / browser dashboard / Python caller
             |
             v
      codex_subscription
       |      |       |
       |      |       +-- Local dashboard (loopback only)
       |      +---------- OpenAI-compatible API (loopback + application Bearer keys)
       +----------------- OAuth and subscription client
                              |
                              v
                    OpenAI authentication/backend
```

## Modules

- `auth.py`: PKCE login, callback listener, token persistence, single-flight refresh, and
  status.
- `api_keys.py`: application-key metadata in SQLite, recoverable secrets in macOS Keychain,
  legacy-key migration, model/reasoning permission policies, lifecycle operations, and live
  request authentication.
- `client.py`: Codex client profile, model discovery, multimodal request conversion, retry,
  incremental SSE iteration, response aggregation, and usage parsing.
- `transport.py`: shared HTTPS opener with a packaged CA bundle and `SSL_CERT_FILE` override.
- `server.py`: local OpenAI-compatible HTTP facade, live Responses/Chat SSE forwarding,
  per-key model/reasoning authorization, bounded upstream concurrency, and browser-extension
  CORS policy.
- `service.py`: shared background API lifecycle, authenticated control probes, and detached
  process management for both CLI and dashboard.
- `settings.py`: shared, permission-restricted defaults for CLI and dashboard entry points.
- `terminal_menu.py`: dependency-free arrow-key selector for interactive CLI configuration.
- `ui.py`: responsive API Console and API Keys views, per-key permission editor, multimodal
  direct/API test workbench, usage metrics, API process management, and dashboard session
  protection.
- `cli.py`: the `csub` entry point with `login`, `status`, `models`, `config`, `keys`, `ask`,
  `serve`, and `ui` commands.

The reusable library is under `src/codex_subscription`. PyInstaller entry points under
`packaging` are intentionally thin so the same modules power source installs and standalone
artifacts. The CLI is installed as an `onedir` runtime behind the lightweight `csub` launcher
to avoid per-command extraction overhead.

The dashboard and `csub start/stop/restart` control the same detached API process. Closing the
dashboard only stops its own HTTP server; `csub serve` remains an explicit foreground debugging
mode.

Service-control routes continue to require the private compatibility/control key. Model routes
authenticate against the shared SQLite key registry, so creating, disabling, or deleting an
application key in the dashboard takes effect without restarting the API process. The registry
stores only SHA-256 fingerprints and metadata; full application secrets are retrieved from
macOS Keychain only for explicit local reveal operations.

For streaming requests, the handler keeps one upstream HTTPS response open and flushes every SSE
event to the loopback client. Closing the downstream iterator closes that upstream response. The
same bounded semaphore covers streaming, non-streaming, and model-list requests so a long-running
stream retains its slot until completion or cancellation.
