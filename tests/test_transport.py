from __future__ import annotations

import os
import unittest
import urllib.request
from unittest.mock import Mock, patch

from codex_subscription.transport import _ca_file, _ssl_context, urlopen


class TransportTests(unittest.TestCase):
    def tearDown(self) -> None:
        _ssl_context.cache_clear()

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
