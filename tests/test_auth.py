from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path

from codex_subscription.auth import (
    FileTokenStore,
    OAuthTokens,
    create_pkce_pair,
    extract_chatgpt_account_id,
)


class AuthTests(unittest.TestCase):
    def test_pkce_pair_has_url_safe_challenge(self) -> None:
        verifier, challenge = create_pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertNotIn("=", challenge)
        self.assertNotIn("+", challenge)
        self.assertNotIn("/", challenge)

    def test_file_token_store_round_trip_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "auth.json"
            store = FileTokenStore(path)
            tokens = OAuthTokens("access", "refresh", int(time.time()) + 3600)
            store.save(tokens)

            self.assertEqual(store.load(), tokens)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_extract_chatgpt_account_id(self) -> None:
        payload = {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "account-123",
            }
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        token = f"header.{encoded}.signature"
        self.assertEqual(extract_chatgpt_account_id(token), "account-123")


if __name__ == "__main__":
    unittest.main()
