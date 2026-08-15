from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_subscription.api_keys import (
    ApiKeyStore,
    FileSecretStore,
    MemorySecretStore,
    default_secret_store,
)


class ApiKeyStoreTests(unittest.TestCase):
    def test_file_secret_store_round_trip_permissions_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "secrets"
            secrets = FileSecretStore(root)
            secrets.put("account-123", "secret-value")
            secret_path = root / "account-123"

            self.assertEqual(secrets.get("account-123"), "secret-value")
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(secret_path.stat().st_mode & 0o777, 0o600)
            secrets.delete("account-123")
            self.assertFalse(secret_path.exists())

    def test_file_secret_store_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secrets = FileSecretStore(Path(directory) / "secrets")
            with self.assertRaisesRegex(ValueError, "账户标识无效"):
                secrets.put("../outside", "secret-value")

    def test_linux_uses_file_secret_store_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "codex_subscription.api_keys.sys.platform", "linux"
                ),
                patch(
                    "codex_subscription.api_keys.DEFAULT_FILE_SECRETS_PATH",
                    Path(directory) / "secrets",
                ),
            ):
                secrets = default_secret_store()
                self.assertIsInstance(secrets, FileSecretStore)
                self.assertEqual(secrets.path, Path(directory) / "secrets")

    def test_create_reveal_authenticate_and_disable_without_plaintext_db(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api_keys.db"
            secrets = MemorySecretStore()
            store = ApiKeyStore(path, secrets)
            record, secret = store.create("browser-translator")

            self.assertTrue(secret.startswith(f"{record.prefix}_"))
            self.assertEqual(store.reveal(record.id), secret)
            self.assertNotIn(secret.encode(), path.read_bytes())
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

            authenticated = store.authenticate(secret)
            self.assertIsNotNone(authenticated)
            assert authenticated is not None
            self.assertEqual(authenticated.id, record.id)
            self.assertEqual(authenticated.request_count, 1)
            self.assertIsNotNone(authenticated.last_used_at)

            store.set_enabled(record.prefix, False)
            self.assertIsNone(store.authenticate(secret))
            store.set_enabled(record.id, True)
            self.assertIsNotNone(store.authenticate(secret))

    def test_legacy_key_is_recoverable_and_protected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApiKeyStore(
                Path(directory) / "api_keys.db", MemorySecretStore()
            )
            secret = "legacy-local-api-key-1234567890"
            first = store.ensure_legacy_key(secret)
            second = store.ensure_legacy_key(secret)

            self.assertEqual(first.id, second.id)
            self.assertTrue(first.is_system)
            self.assertEqual(store.reveal(first.id), secret)
            with self.assertRaisesRegex(ValueError, "不能禁用"):
                store.set_enabled(first.id, False)
            with self.assertRaisesRegex(ValueError, "不能删除"):
                store.delete(first.id)

    def test_rename_and_delete_remove_metadata_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secrets = MemorySecretStore()
            store = ApiKeyStore(Path(directory) / "api_keys.db", secrets)
            record, _ = store.create("agent-a")

            renamed = store.rename(record.id[:10], "agent-b")
            self.assertEqual(renamed.name, "agent-b")
            store.delete(record.prefix)

            self.assertEqual(store.list(), [])
            self.assertNotIn(record.id, secrets.values)

    def test_model_and_reasoning_permissions_are_enforced_and_replaceable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ApiKeyStore(
                Path(directory) / "api_keys.db", MemorySecretStore()
            )
            record, _ = store.create(
                "restricted-agent",
                {"gpt-5.6-luna": ["low", "medium"]},
            )

            self.assertTrue(record.allows("gpt-5.6-luna", "low"))
            self.assertFalse(record.allows("gpt-5.6-luna", "high"))
            self.assertFalse(record.allows("gpt-5.6-sol", "low"))
            self.assertEqual(
                record.public()["permissions"],
                {"gpt-5.6-luna": ["low", "medium"]},
            )

            unrestricted = store.set_permissions(record.id, None)
            self.assertTrue(unrestricted.allows("any-model", "ultra"))
            with self.assertRaisesRegex(ValueError, "至少需要选择一个模型"):
                store.set_permissions(record.id, {})

    def test_existing_database_migrates_to_unrestricted_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api_keys.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE api_keys (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        prefix TEXT NOT NULL UNIQUE,
                        secret_hash TEXT NOT NULL UNIQUE,
                        enabled INTEGER NOT NULL,
                        is_system INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_used_at TEXT,
                        request_count INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO api_keys VALUES (
                        'old-key', 'old app', 'csub_live_old', 'hash', 1, 0,
                        '2026-01-01T00:00:00+00:00',
                        '2026-01-01T00:00:00+00:00', NULL, 0
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            store = ApiKeyStore(path, MemorySecretStore())
            record = store.get("old-key")

            self.assertIsNone(record.permissions)
            migrated = sqlite3.connect(path)
            try:
                columns = {
                    row[1] for row in migrated.execute("PRAGMA table_info(api_keys)")
                }
            finally:
                migrated.close()
            self.assertIn("permissions_json", columns)


if __name__ == "__main__":
    unittest.main()
