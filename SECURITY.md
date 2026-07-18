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

OAuth tokens and the local API key are sensitive credentials stored in user-only files.
Anyone who can read those files or execute code as the same operating-system user should
be considered able to use the subscription.
