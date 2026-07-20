from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from codex_subscription.ui import (
    DASHBOARD_HTML,
    DashboardController,
    DashboardServer,
    _dashboard_is_running,
    launch_dashboard,
)
from codex_subscription.client import CodexResponse, CodexSubscriptionClient
from codex_subscription.server import SubscriptionApiServer


class _NoopClient:
    def list_models(self):
        return []


class DashboardControllerTests(unittest.TestCase):
    def test_dashboard_exposes_multimodal_dual_path_workbench(self) -> None:
        self.assertIn('id="testMode"', DASHBOARD_HTML)
        self.assertIn('value="local_api"', DASHBOARD_HTML)
        self.assertIn('id="imageInput"', DASHBOARD_HTML)
        self.assertIn('value="responses"', DASHBOARD_HTML)

    def test_defaults_are_ready_for_local_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")

        self.assertEqual(controller.config["host"], "127.0.0.1")
        self.assertEqual(controller.config["port"], 8317)
        self.assertEqual(controller.config["model"], "gpt-5.6-luna")
        self.assertEqual(controller.config["reasoning_effort"], "low")
        self.assertNotEqual(controller.config["api_key"], "codex-local-translate")
        self.assertGreaterEqual(len(controller.config["api_key"]), 32)

    def test_legacy_predictable_key_is_rotated(self) -> None:
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
            controller = DashboardController(path)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertNotEqual(controller.config["api_key"], "codex-local-translate")
        self.assertEqual(saved["api_key"], controller.config["api_key"])

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

    def test_legacy_dashboard_page_is_detected_without_health_endpoint(self) -> None:
        page_response = MagicMock()
        page_response.__enter__.return_value.read.return_value = (
            b"<title>Codex Subscription</title><h1>Codex Subscription</h1>"
        )
        with patch(
            "codex_subscription.ui.urllib.request.urlopen",
            side_effect=[OSError("health endpoint unavailable"), page_response],
        ):
            self.assertTrue(_dashboard_is_running("http://127.0.0.1:8320"))

    @patch("codex_subscription.ui._dashboard_is_running", return_value=False)
    @patch("codex_subscription.ui.DashboardServer")
    def test_unrelated_port_conflict_has_actionable_error(
        self,
        server_class: MagicMock,
        dashboard_running: MagicMock,
    ) -> None:
        server_class.side_effect = OSError(errno.EADDRINUSE, "Address already in use")

        with self.assertRaisesRegex(ValueError, "csub ui --port 8321"):
            launch_dashboard(open_browser=False)

    def test_dashboard_api_requires_page_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            server = DashboardServer(("127.0.0.1", 0), controller)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base_url}/") as response:
                    cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                    self.assertIn(
                        "frame-ancestors 'none'",
                        response.headers["Content-Security-Policy"],
                    )

                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(f"{base_url}/api/state")
                self.assertEqual(context.exception.code, 401)
                context.exception.close()

                request = urllib.request.Request(
                    f"{base_url}/api/state", headers={"Cookie": cookie}
                )
                with urllib.request.urlopen(request) as response:
                    state = json.loads(response.read().decode("utf-8"))
                self.assertNotIn("account_id", state["auth"])
                self.assertNotIn("token_path", state["auth"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_dashboard_rejects_cross_site_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            server = DashboardServer(("127.0.0.1", 0), controller)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base_url}/") as response:
                    cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                request = urllib.request.Request(
                    f"{base_url}/api/server/stop",
                    data=b"{}",
                    method="POST",
                    headers={
                        "Cookie": cookie,
                        "Content-Type": "application/json",
                        "Origin": "https://example.invalid",
                        "X-Codex-Dashboard": "1",
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                self.assertEqual(context.exception.code, 403)
                context.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_quitting_dashboard_does_not_stop_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            server = DashboardServer(("127.0.0.1", 0), controller)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base_url}/") as response:
                    cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                request = urllib.request.Request(
                    f"{base_url}/api/quit",
                    data=b"{}",
                    method="POST",
                    headers={
                        "Cookie": cookie,
                        "Content-Type": "application/json",
                        "X-Codex-Dashboard": "1",
                    },
                )
                with patch.object(controller, "stop_api") as stop_api:
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(response.status, 200)
                    thread.join(timeout=2)
                    stop_api.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_external_api_server_is_reported_as_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            api_server = SubscriptionApiServer(
                ("127.0.0.1", 0),
                _NoopClient(),
                api_key=controller.config["api_key"],
            )
            api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
            api_thread.start()
            try:
                controller.config["port"] = api_server.server_port
                server_state = controller.state()["server"]

                self.assertTrue(server_state["running"])
                self.assertEqual(server_state["status"], "running")
                self.assertTrue(server_state["port_in_use"])
            finally:
                api_server.shutdown()
                api_server.server_close()
                api_thread.join(timeout=2)

    def test_direct_test_returns_request_response_and_image_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            client = MagicMock()
            client.generate_response.return_value = CodexResponse(
                text="图片中有一个窗口。",
                response_id="resp-direct",
                model="gpt-test",
            )
            with patch.object(controller, "_client", return_value=client):
                result = controller.test(
                    {
                        "mode": "direct",
                        "text": "描述图片",
                        "instructions": "使用中文",
                        "model": "gpt-test",
                        "reasoning_effort": "medium",
                        "images": [
                            {
                                "data_url": "data:image/png;base64,AA==",
                            }
                        ],
                    }
                )

        self.assertEqual(result["text"], "图片中有一个窗口。")
        self.assertEqual(result["image_count"], 1)
        self.assertEqual(result["response"]["id"], "resp-direct")
        self.assertIn("base64", result["request"]["input"][0]["content"][1]["image_url"])
        client.generate_response.assert_called_once()

    def test_local_api_test_reaches_both_compatible_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            base_client = CodexSubscriptionClient(allow_login=False)
            api_server = SubscriptionApiServer(
                ("127.0.0.1", 0),
                base_client,
                api_key=controller.config["api_key"],
            )
            api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
            api_thread.start()
            try:
                controller.config["port"] = api_server.server_port
                with patch.object(
                    CodexSubscriptionClient,
                    "create_response",
                    return_value=CodexResponse(
                        text="API 链路成功",
                        response_id="resp-api",
                        model="gpt-test",
                    ),
                ):
                    chat_result = controller.test(
                        {
                            "mode": "local_api",
                            "api_format": "chat",
                            "text": "测试 API",
                            "model": "gpt-test",
                            "reasoning_effort": "low",
                        }
                    )
                    responses_result = controller.test(
                        {
                            "mode": "local_api",
                            "api_format": "responses",
                            "text": "测试 Responses API",
                            "model": "gpt-test",
                            "reasoning_effort": "low",
                        }
                    )
            finally:
                api_server.shutdown()
                api_server.server_close()
                api_thread.join(timeout=2)

        self.assertEqual(chat_result["status"], 200)
        self.assertEqual(chat_result["text"], "API 链路成功")
        self.assertIn("/v1/chat/completions", chat_result["endpoint"])
        self.assertEqual(chat_result["response"]["object"], "chat.completion")
        self.assertEqual(responses_result["status"], 200)
        self.assertEqual(responses_result["text"], "API 链路成功")
        self.assertIn("/v1/responses", responses_result["endpoint"])
        self.assertEqual(responses_result["response"]["object"], "response")


if __name__ == "__main__":
    unittest.main()
