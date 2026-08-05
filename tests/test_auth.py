from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_subscription.auth import (
    CodexOAuth,
    FileTokenStore,
    OAuthTokens,
    create_pkce_pair,
    extract_chatgpt_account_id,
    extract_chatgpt_identity,
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
        encoded = (
            base64.urlsafe_b64encode(json.dumps(payload).encode())
            .rstrip(b"=")
            .decode()
        )
        token = f"header.{encoded}.signature"
        self.assertEqual(extract_chatgpt_account_id(token), "account-123")

    def test_extract_chatgpt_identity(self) -> None:
        payload = {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "account-123",
                "chatgpt_plan_type": "prolite",
            },
            "https://api.openai.com/profile": {
                "name": "Test User",
                "email": "test@example.com",
            },
        }
        encoded = (
            base64.urlsafe_b64encode(json.dumps(payload).encode())
            .rstrip(b"=")
            .decode()
        )
        token = f"header.{encoded}.signature"

        identity = extract_chatgpt_identity(token)

        self.assertEqual(identity.account_id, "account-123")
        self.assertEqual(identity.display_name, "Test User")
        self.assertEqual(identity.email, "test@example.com")
        self.assertEqual(identity.plan_type, "prolite")

    def test_expired_token_is_refreshed_once_across_threads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FileTokenStore(Path(directory) / "auth.json")
            store.save(OAuthTokens("old", "refresh", int(time.time()) - 1))
            auth = CodexOAuth(store=store)
            refreshed = OAuthTokens("new", "refresh-2", int(time.time()) + 3600)
            with patch.object(
                auth, "_request_tokens", return_value=refreshed
            ) as request_tokens:
                results: list[str] = []
                threads = [
                    threading.Thread(
                        target=lambda: results.append(
                            auth.get_access_token(allow_login=False)
                        )
                    )
                    for _ in range(5)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=2)

        self.assertEqual(results, ["new"] * 5)
        request_tokens.assert_called_once()


if __name__ == "__main__":
    unittest.main()
