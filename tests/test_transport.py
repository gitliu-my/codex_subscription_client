from __future__ import annotations

import os
import socket
import unittest
import urllib.request
from unittest.mock import Mock, patch

from codex_subscription.transport import (
    _SYSTEM_GETADDRINFO,
    _ca_file,
    _configure_address_family,
    _ipv4_getaddrinfo,
    _ssl_context,
    urlopen,
)


class TransportTests(unittest.TestCase):
    def tearDown(self) -> None:
        _ssl_context.cache_clear()
        socket.getaddrinfo = _SYSTEM_GETADDRINFO

    @patch.dict(os.environ, {"CODEX_SUBSCRIPTION_FORCE_IPV4": "1"})
    @patch("codex_subscription.transport.socket.getaddrinfo")
    def test_force_ipv4_can_be_enabled(self, getaddrinfo: Mock) -> None:
        _configure_address_family()

        self.assertIs(socket.getaddrinfo, _ipv4_getaddrinfo)
        getaddrinfo.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    def test_default_address_family_restores_system_resolver(self) -> None:
        socket.getaddrinfo = _ipv4_getaddrinfo

        _configure_address_family()

        self.assertIsNot(socket.getaddrinfo, _ipv4_getaddrinfo)

    @patch("codex_subscription.transport._SYSTEM_GETADDRINFO")
    def test_ipv4_resolver_restricts_unspecified_lookups(
        self, system_getaddrinfo: Mock
    ) -> None:
        system_getaddrinfo.return_value = []

        self.assertEqual(_ipv4_getaddrinfo("example.com", 443), [])

        system_getaddrinfo.assert_called_once_with(
            "example.com", 443, socket.AF_INET, 0, 0, 0
        )

    @patch.dict(os.environ, {}, clear=True)
    @patch("codex_subscription.transport.certifi.where", return_value="/bundle/cacert.pem")
    def test_bundled_ca_is_the_default(self, certifi_where: Mock) -> None:
        self.assertEqual(_ca_file(), "/bundle/cacert.pem")
        certifi_where.assert_called_once_with()

    @patch.dict(os.environ, {"SSL_CERT_FILE": "/private/company-ca.pem"})
    def test_environment_can_override_bundled_ca(self) -> None:
        self.assertEqual(_ca_file(), "/private/company-ca.pem")

    @patch("codex_subscription.transport.urllib.request.urlopen")
    @patch("codex_subscription.transport._ssl_context")
    def test_urlopen_uses_explicit_ca_context(
        self, ssl_context: Mock, urllib_urlopen: Mock
    ) -> None:
        request = urllib.request.Request("https://example.com")
        context = Mock()
        ssl_context.return_value = context

        urlopen(request, timeout=12)

        urllib_urlopen.assert_called_once_with(request, timeout=12, context=context)


if __name__ == "__main__":
    unittest.main()
