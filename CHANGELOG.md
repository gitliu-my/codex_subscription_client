# Changelog

All notable changes are documented here.

## Unreleased

## 0.7.0 - 2026-07-22

- Forward upstream Responses SSE events as they arrive and convert live text/tool deltas for
  Chat Completions clients.
- Close upstream response streams when downstream clients disconnect.
- Parse and expose upstream token usage in Responses and Chat Completions payloads.
- Forward additional Responses request fields to the subscription backend.
- Serialize OAuth refreshes across concurrent requests and reuse newly refreshed credentials.
- Add a configurable upstream concurrency limit with bounded queueing and `429` responses.
- Add a real streaming toggle to the dashboard workbench for both direct subscription and local
  API tests.
- Show first-token latency, total latency, live/final output speed, and input/output/total token
  usage in the workbench.
- Add optional Responses image generation with quality/size controls and ordered mixed
  text/image rendering, including image downloads.
- Summarize generated-image Base64 data in raw event views to keep the dashboard responsive.

## 0.6.0 - 2026-07-20

- Redesign the local dashboard for wide desktop screens and responsive mobile use.
- Add text and image inputs to the dashboard test workbench.
- Test either the subscription backend directly or the running local API service.
- Test both `/v1/responses` and `/v1/chat/completions` with visible request metadata,
  raw responses, HTTP status, and latency.
- Add regression coverage for multimodal dashboard requests and both compatible API paths.

## 0.5.1 - 2026-07-20

- Bundle and explicitly use a trusted CA certificate store for OAuth and model HTTPS requests.
- Preserve `SSL_CERT_FILE` overrides for private or enterprise certificate authorities.

## 0.5.0 - 2026-07-20

- Rename the terminal command to `csub` without retaining the old command alias.
- Add an arrow-key terminal menu for choosing the default model and reasoning effort.
- Share saved defaults between `config`, `ask`, `serve`, and the macOS dashboard.
- Make terminal API startup reuse the saved port and stable local API key.
- Preserve command-line and environment-variable overrides for automation.
- Reopen an existing legacy dashboard and report unrelated port conflicts without a traceback.
- Install the standalone CLI as an `onedir` runtime for substantially faster startup.
- Show friendly help with no arguments and include the local API state in `csub status`.
- Add a single detached API service controlled by both `csub start/stop/restart` and the UI.
- Keep the API running when the dashboard exits.
- Remove the redundant macOS App launcher in favor of `csub ui`.
- Add Apple Silicon release packaging and automated Homebrew Formula publishing.

## 0.4.0 - 2026-07-18

- Require a local Bearer key for every model API request.
- Generate a random key by default and rotate the legacy predictable dashboard key.
- Restrict CORS to browser extension origins and explicitly configured trusted origins.
- Protect dashboard APIs with a random session cookie, origin checks, and a CSRF header.
- Hide account identifiers and credential paths from dashboard state.
- Add MIT licensing, security and contribution policies, architecture docs, and CI.

## 0.3.0 - 2026-07-17

- Add the standalone macOS app, local dashboard, and external server detection.
