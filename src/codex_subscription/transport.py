from __future__ import annotations

"""Shared HTTPS transport with a CA bundle that survives standalone packaging."""

import os
import ssl
import urllib.request
from functools import lru_cache
from typing import Any

import certifi


def urlopen(
    request: str | urllib.request.Request,
    *,
    timeout: float,
) -> Any:
    return urllib.request.urlopen(
        request,
        timeout=timeout,
        context=_ssl_context(_ca_file()),
    )


def _ca_file() -> str:
    return os.environ.get("SSL_CERT_FILE") or certifi.where()


@lru_cache(maxsize=4)
def _ssl_context(ca_file: str) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_file)
