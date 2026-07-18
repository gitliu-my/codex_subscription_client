from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from codex_subscription.ui import (
    DashboardController,
    DashboardServer,
    _dashboard_is_running,
)
from codex_subscription.server import SubscriptionApiServer


class _NoopClient:
    def list_models(self):
        return []


class DashboardControllerTests(unittest.TestCase):
    def test_defaults_are_ready_for_local_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")

        self.assertEqual(controller.config["host"], "127.0.0.1")
        self.assertEqual(controller.config["port"], 8317)
        self.assertEqual(controller.config["model"], "gpt-5.6-luna")
        self.assertEqual(controller.config["reasoning_effort"], "low")

    def test_settings_are_saved_with_user_only_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "settings.json"
            controller = DashboardController(path)
            controller.config["model"] = "gpt-test"
            controller._save_settings()

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["model"], "gpt-test")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_non_loopback_host_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            with self.assertRaisesRegex(ValueError, "只允许 API 监听本机"):
                controller._validated_config({**controller.config, "host": "0.0.0.0"})

    def test_running_dashboard_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            server = DashboardServer(("127.0.0.1", 0), controller)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertTrue(
                    _dashboard_is_running(f"http://127.0.0.1:{server.server_port}")
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_external_api_server_is_reported_as_running(self) -> None:
        api_server = SubscriptionApiServer(("127.0.0.1", 0), _NoopClient())
        api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
        api_thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                controller = DashboardController(Path(directory) / "settings.json")
                controller.config["port"] = api_server.server_port
                server_state = controller.state()["server"]

            self.assertTrue(server_state["running"])
            self.assertTrue(server_state["external"])
            self.assertFalse(server_state["managed"])
            self.assertTrue(server_state["port_in_use"])
        finally:
            api_server.shutdown()
            api_server.server_close()
            api_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
