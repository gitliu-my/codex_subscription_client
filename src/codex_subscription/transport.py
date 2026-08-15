from __future__ import annotations

"""Shared HTTPS transport with a CA bundle that survives standalone packaging."""

import os
import socket
import ssl
import urllib.request
from functools import lru_cache
from typing import Any

import certifi


_SYSTEM_GETADDRINFO = socket.getaddrinfo


def urlopen(
    request: str | urllib.request.Request,
    *,
    timeout: float,
) -> Any:
    _configure_address_family()
    return urllib.request.urlopen(
        request,
        timeout=timeout,
        context=_ssl_context(_ca_file()),
    )


def _configure_address_family() -> None:
    """Optionally avoid broken IPv6 routes in the current process."""
    force_ipv4 = os.environ.get("CODEX_SUBSCRIPTION_FORCE_IPV4", "").lower()
    socket.getaddrinfo = (
        _ipv4_getaddrinfo
        if force_ipv4 in {"1", "true", "yes", "on"}
        else _SYSTEM_GETADDRINFO
    )


def _ipv4_getaddrinfo(
    host: str | bytes | None,
    port: str | int | None,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[Any, ...]]:
    effective_family = socket.AF_INET if family in {0, socket.AF_UNSPEC} else family
    return _SYSTEM_GETADDRINFO(
        host,
        port,
        effective_family,
        type,
        proto,
        flags,
    )


def _ca_file() -> str:
    return os.environ.get("SSL_CERT_FILE") or certifi.where()


@lru_cache(maxsize=4)
def _ssl_context(ca_file: str) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_file)
