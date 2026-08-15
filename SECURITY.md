# Security Policy

## Supported version

Only the current `main` branch is actively maintained while the project is in alpha.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or a private Security Advisory for
this repository. Do not open a public Issue for an unpatched vulnerability.

Reports should include the affected version, reproduction steps, expected impact, and a
minimal proof of concept. Never include real OAuth tokens, local API keys, account IDs,
or unredacted credential files. Replace them with synthetic values before submitting.

## Local security boundary

This project is designed for one user on one trusted machine. The API and dashboard must
remain bound to loopback. Exposing either service through a reverse proxy, public tunnel,
container port mapping, or LAN bind is outside the supported threat model.

OAuth tokens, application API keys, and the compatibility/control key are sensitive credentials.
Application-key secrets are stored in macOS Keychain or Linux user-only secret files while
metadata uses a user-only SQLite database. The compatibility key and OAuth tokens remain in
user-only files. Anyone who can run code as the same operating-system user should still be
considered able to use the subscription.
