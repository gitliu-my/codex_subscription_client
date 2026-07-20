# Security Model

## Protected assets

- OAuth access and refresh tokens.
- ChatGPT account identifier encoded in the access token.
- Local API key.
- Model requests and responses.

## Trust assumptions

The operating-system user and processes running as that user are trusted. The project does
not attempt to isolate one local process from another process with the same user privileges.
The remote authentication and model backends are trusted to enforce subscription access.

## Network boundaries

- The API and dashboard bind to loopback only.
- Model endpoints require a Bearer key. `/health` reveals only `{"status":"ok"}`.
- CORS reflects browser-extension origins and explicitly configured origins; arbitrary web
  origins are rejected.
- Dashboard APIs require a random HttpOnly SameSite session cookie. Mutating requests also
  require a same-origin Host/Origin, JSON content type, and `X-Codex-Dashboard: 1`.
- Dashboard pages deny framing and use a restrictive Content Security Policy.
- Outbound OAuth and model HTTPS requests verify certificates against the packaged CA bundle.
  `SSL_CERT_FILE` can replace it when an environment requires a private certificate authority.

## Credential storage

Credentials are stored as JSON files under `~/.codex_subscription` with directory mode
`0700` and file mode `0600`. They are not placed in app bundles or repository files. This is
filesystem permission protection, not hardware-backed or macOS Keychain storage.

## Out of scope

- A compromised local user account or process running as that user.
- Users deliberately exposing loopback services through a proxy or tunnel.
- Changes to unsupported upstream protocols, model availability, or provider policy.
- Abuse prevention beyond the limits enforced by the subscription backend.
