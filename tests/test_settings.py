from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_subscription.settings import SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_update_preserves_unrelated_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            initial = store.load_or_create()
            updated = store.update(model="gpt-test", reasoning_effort="high")

            self.assertEqual(updated["api_key"], initial["api_key"])
            self.assertEqual(updated["host"], initial["host"])
            self.assertEqual(updated["port"], initial["port"])
            self.assertEqual(updated["model"], "gpt-test")
            self.assertEqual(updated["reasoning_effort"], "high")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_legacy_key_is_migrated_by_shared_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "host": "127.0.0.1",
                        "port": 8317,
                        "api_key": "codex-local-translate",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "low",
                    }
                ),
                encoding="utf-8",
            )
            config = SettingsStore(path).load_or_create()

        self.assertNotEqual(config["api_key"], "codex-local-translate")
        self.assertGreaterEqual(len(config["api_key"]), 32)


if __name__ == "__main__":
    unittest.main()
