# Security Model

## Protected assets

- OAuth access and refresh tokens.
- ChatGPT profile fields and account identifier encoded in the access token.
- Application API keys and the compatibility/control key.
- Model requests and responses.

## Trust assumptions

The operating-system user and processes running as that user are trusted. The project does
not attempt to isolate one local process from another process with the same user privileges.
The remote authentication and model backends are trusted to enforce subscription access.

## Network boundaries

- The API and dashboard bind to loopback only.
- Model endpoints require an enabled application Bearer key. Internal control endpoints accept
  only the compatibility/control key. `/health` reveals only `{"status":"ok"}`.
- Restricted application keys expose only their allowed models through `/v1/models`; Responses
  and Chat Completions requests with a disallowed model or reasoning effort fail with `403`
  before an upstream request is created.
- CORS reflects browser-extension origins and explicitly configured origins; arbitrary web
  origins are rejected.
- Dashboard APIs require a random HttpOnly SameSite session cookie. Mutating requests also
  require a same-origin Host/Origin, JSON content type, and `X-Codex-Dashboard: 1`.
- Display name, email, raw plan type, and account ID are returned only by the authenticated local
  dashboard state endpoint. OAuth tokens and credential paths are never included.
- Dashboard pages deny framing and use a restrictive Content Security Policy.
- Outbound OAuth and model HTTPS requests verify certificates against the packaged CA bundle.
  `SSL_CERT_FILE` can replace it when an environment requires a private certificate authority.

## Credential storage

OAuth credentials and shared settings are stored as JSON files under `~/.codex_subscription`
with directory mode `0700` and file mode `0600`. Application-key metadata is stored in a `0600`
SQLite database containing only SHA-256 fingerprints, state, and usage metadata. Recoverable
application-key secrets are stored as generic-password items in macOS Keychain. Model and
reasoning permission allowlists are non-secret metadata in the same SQLite database. The default
compatibility key remains in the permission-restricted settings file during the compatibility
migration period.

## Out of scope

- A compromised local user account or process running as that user.
- Users deliberately exposing loopback services through a proxy or tunnel.
- Changes to unsupported upstream protocols, model availability, or provider policy.
- Abuse prevention beyond the limits enforced by the subscription backend.
- Per-key rate limiting and token quotas; these remain future work.
