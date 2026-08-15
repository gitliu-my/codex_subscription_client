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

from codex_subscription.api_keys import ApiKeyStore, MemorySecretStore
from codex_subscription.auth import AuthStatus
from codex_subscription.ui import (
    DASHBOARD_HTML,
    DashboardController,
    DashboardServer,
    _dashboard_is_running,
    _output_tokens_per_second,
    launch_dashboard,
)
from codex_subscription.client import CodexResponse, CodexSubscriptionClient
from codex_subscription.server import SubscriptionApiServer


class _NoopClient:
    def list_models(self):
        return []


class DashboardControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key_directory = tempfile.TemporaryDirectory()
        self.api_keys = ApiKeyStore(
            Path(self.key_directory.name) / "api_keys.db", MemorySecretStore()
        )
        self.api_keys_patch = patch(
            "codex_subscription.ui.ApiKeyStore", return_value=self.api_keys
        )
        self.api_keys_patch.start()

    def tearDown(self) -> None:
        self.api_keys_patch.stop()
        self.key_directory.cleanup()

    def test_dashboard_exposes_multimodal_dual_path_workbench(self) -> None:
        self.assertIn('id="serviceView"', DASHBOARD_HTML)
        self.assertIn('id="testMode"', DASHBOARD_HTML)
        self.assertIn('id="testModel"', DASHBOARD_HTML)
        self.assertIn('id="testEffort"', DASHBOARD_HTML)
        self.assertIn('id="maxOutputTokens"', DASHBOARD_HTML)
        self.assertIn('value="local_api"', DASHBOARD_HTML)
        self.assertIn('id="imageInput"', DASHBOARD_HTML)
        self.assertIn('value="responses"', DASHBOARD_HTML)
        self.assertIn('id="maxConcurrency"', DASHBOARD_HTML)
        self.assertIn('id="streamMode"', DASHBOARD_HTML)
        self.assertIn('id="metricUsage"', DASHBOARD_HTML)
        self.assertIn('id="metricRate"', DASHBOARD_HTML)
        self.assertIn('id="imageGeneration"', DASHBOARD_HTML)
        self.assertIn('id="metricGeneratedImages"', DASHBOARD_HTML)
        self.assertIn('id="keyList"', DASHBOARD_HTML)
        self.assertIn('id="keyDialog"', DASHBOARD_HTML)
        self.assertIn('id="permissionDialog"', DASHBOARD_HTML)
        self.assertIn("function revealApiKey", DASHBOARD_HTML)
        self.assertIn("function editApiKeyPermissions", DASHBOARD_HTML)
        self.assertIn("/api/keys/permissions", DASHBOARD_HTML)
        self.assertIn("response.body.getReader()", DASHBOARD_HTML)
        self.assertIn("function liveRate", DASHBOARD_HTML)
        self.assertIn("function renderOutput", DASHBOARD_HTML)
        self.assertIn("/api/server/configure", DASHBOARD_HTML)
        self.assertIn('<code id="chatEndpoint">', DASHBOARD_HTML)
        self.assertIn('id="serverToggleBtn"', DASHBOARD_HTML)
        self.assertIn('id="authToggleBtn"', DASHBOARD_HTML)
        self.assertIn('id="authProfileBtn"', DASHBOARD_HTML)
        self.assertIn('id="profileEmail"', DASHBOARD_HTML)
        self.assertIn('id="profilePlan"', DASHBOARD_HTML)
        self.assertIn('id="profileAccountId"', DASHBOARD_HTML)
        self.assertIn("function toggleServer", DASHBOARD_HTML)
        self.assertIn("function toggleAuth", DASHBOARD_HTML)
        self.assertIn("function toggleAuthProfile", DASHBOARD_HTML)
        self.assertIn(".lab-head{position:sticky", DASHBOARD_HTML)
        self.assertIn("type==='error'?3600", DASHBOARD_HTML)
        self.assertIn("$('newKeyName').focus()", DASHBOARD_HTML)
        self.assertNotIn('id="endpoint"', DASHBOARD_HTML)
        self.assertNotIn('id="startBtn"', DASHBOARD_HTML)
        self.assertNotIn('id="stopBtn"', DASHBOARD_HTML)
        self.assertNotIn('id="loginBtn"', DASHBOARD_HTML)
        self.assertNotIn('id="logoutBtn"', DASHBOARD_HTML)
        self.assertNotIn("仅监听本机，不向局域网开放", DASHBOARD_HTML)

    def test_dashboard_separates_service_debugger_and_key_views(self) -> None:
        service_view = DASHBOARD_HTML.index('id="serviceView"')
        console_view = DASHBOARD_HTML.index('id="consoleView"')
        workbench = DASHBOARD_HTML.index('class="panel lab"')
        keys_view = DASHBOARD_HTML.index('id="keysView"')
        key_list = DASHBOARD_HTML.index('id="keyList"')

        self.assertLess(service_view, console_view)
        self.assertLess(console_view, workbench)
        self.assertLess(workbench, keys_view)
        self.assertLess(keys_view, key_list)
        self.assertIn('data-app-view="service"', DASHBOARD_HTML)
        self.assertIn('data-app-view="console"', DASHBOARD_HTML)
        self.assertIn('data-app-view="keys"', DASHBOARD_HTML)
        self.assertIn("function showAppView", DASHBOARD_HTML)

    @patch("codex_subscription.ui.start_api_service")
    @patch("codex_subscription.ui.stop_api_service")
    @patch("codex_subscription.ui.probe_api")
    def test_configuring_running_api_restarts_with_new_settings(
        self,
        probe: MagicMock,
        stop_service: MagicMock,
        start_service: MagicMock,
    ) -> None:
        probe.return_value = MagicMock(state="running", pid=123)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            controller = DashboardController(path)
            old_config = dict(controller.config)
            new_config = {
                **old_config,
                "port": 8318,
                "reasoning_effort": "medium",
                "max_concurrency": 12,
            }

            result = controller.configure_api(new_config)

            stop_service.assert_called_once_with(old_config)
            start_service.assert_called_once_with(new_config)
            self.assertEqual(controller.config, new_config)
            self.assertEqual(result["config"], new_config)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                new_config,
            )

    def test_output_rate_excludes_reasoning_tokens(self) -> None:
        rate = _output_tokens_per_second(
            {
                "output_tokens": 38,
                "output_tokens_details": {"reasoning_tokens": 10},
            },
            duration_ms=2000,
            first_token_ms=1000,
        )

        self.assertEqual(rate, 28.0)

    def test_defaults_are_ready_for_local_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")

        self.assertEqual(controller.config["host"], "127.0.0.1")
        self.assertEqual(controller.config["port"], 8317)
        self.assertEqual(controller.config["model"], "gpt-5.6-luna")
        self.assertEqual(controller.config["reasoning_effort"], "low")
        self.assertEqual(controller.config["max_concurrency"], 10)
        self.assertNotEqual(controller.config["api_key"], "codex-local-translate")
        self.assertGreaterEqual(len(controller.config["api_key"]), 32)
        self.assertEqual(len(controller.state()["api_keys"]), 1)

    def test_api_keys_can_be_created_revealed_disabled_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            created = controller.create_api_key({"name": "browser-translator"})
            key_id = str(created["key"]["id"])

            self.assertTrue(str(created["secret"]).startswith("csub_live_"))
            self.assertEqual(
                created["key"]["permissions"],
                {"gpt-5.6-luna": ["low"]},
            )
            self.assertNotIn(
                str(created["secret"]),
                json.dumps(controller.state()),
            )
            self.assertEqual(
                controller.reveal_api_key({"id": key_id})["secret"],
                created["secret"],
            )
            permissions = controller.set_api_key_permissions(
                {
                    "id": key_id,
                    "permissions": {
                        "gpt-5.6-luna": ["low", "medium"],
                        "gpt-5.6-sol": ["high"],
                    },
                }
            )
            self.assertEqual(
                permissions["key"]["permissions"]["gpt-5.6-sol"],
                ["high"],
            )
            disabled = controller.set_api_key_enabled(
                {"id": key_id, "enabled": False}
            )
            self.assertFalse(disabled["key"]["enabled"])
            controller.delete_api_key({"id": key_id})
            self.assertEqual(len(controller.state()["api_keys"]), 1)

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
            controller.auth.status = MagicMock(
                return_value=AuthStatus(
                    True,
                    False,
                    Path(directory) / "auth.json",
                    "account-123",
                    "Test User",
                    "test@example.com",
                    "prolite",
                )
            )
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
                self.assertEqual(
                    state["auth"]["profile"],
                    {
                        "display_name": "Test User",
                        "email": "test@example.com",
                        "plan_type": "prolite",
                        "account_id": "account-123",
                    },
                )
                self.assertNotIn("token_path", state["auth"])
                self.assertNotIn("access_token", json.dumps(state))
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

    def test_direct_stream_reports_deltas_first_token_and_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            client = MagicMock()
            client.iter_response_events.return_value = iter(
                [
                    {"type": "response.output_text.delta", "delta": "流式"},
                    {"type": "response.output_text.delta", "delta": "成功"},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp-stream",
                            "model": "gpt-stream",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [
                                        {"type": "output_text", "text": "流式成功"}
                                    ],
                                }
                            ],
                            "usage": {
                                "input_tokens": 12,
                                "output_tokens": 4,
                                "total_tokens": 16,
                            },
                        },
                    },
                ]
            )
            with patch.object(controller, "_client", return_value=client):
                messages = list(
                    controller.test_stream(
                        {
                            "mode": "direct",
                            "text": "测试流式",
                            "model": "gpt-stream",
                            "reasoning_effort": "low",
                        }
                    )
                )

        self.assertEqual(messages[0]["type"], "start")
        self.assertEqual([item["delta"] for item in messages[1:3]], ["流式", "成功"])
        completed = messages[-1]["result"]
        self.assertEqual(completed["text"], "流式成功")
        self.assertEqual(completed["usage"]["total_tokens"], 16)
        self.assertIsNotNone(completed["first_token_ms"])
        self.assertIn("output_tokens_per_second", completed)
        self.assertTrue(completed["request"]["stream"])
        client.iter_response_events.assert_called_once()

    def test_direct_image_generation_returns_mixed_render_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            client = MagicMock()
            client.create_response.return_value = CodexResponse(
                text="这是生成结果。",
                response_id="resp-image",
                model="gpt-test",
                output_items=[
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "这是生成结果。"}
                        ],
                    },
                    {
                        "type": "image_generation_call",
                        "id": "image-1",
                        "result": "aW1hZ2U=",
                    },
                ],
            )
            with patch.object(controller, "_client", return_value=client):
                result = controller.test(
                    {
                        "mode": "direct",
                        "text": "生成一张图片并说明",
                        "model": "gpt-test",
                        "reasoning_effort": "low",
                        "image_generation": True,
                        "image_quality": "low",
                        "image_size": "1024x1024",
                    }
                )

        self.assertEqual(
            [item["type"] for item in result["render_items"]], ["text", "image"]
        )
        self.assertTrue(result["generated_images"][0]["data_url"].startswith("data:image/"))
        self.assertIn("generated image base64", result["response"]["output"][1]["result"])
        request = result["request"]
        self.assertEqual(request["tools"][0]["type"], "image_generation")
        self.assertEqual(request["tools"][0]["quality"], "low")
        call = client.create_response.call_args
        self.assertEqual(call.kwargs["tools"][0]["size"], "1024x1024")

    def test_stream_image_event_is_exposed_without_raw_base64(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            client = MagicMock()
            client.iter_response_events.return_value = iter(
                [
                    {"type": "response.output_text.delta", "delta": "图片如下："},
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "image_generation_call",
                            "id": "image-stream",
                            "result": "aW1hZ2U=",
                        },
                    },
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp-image-stream",
                            "model": "gpt-test",
                            "output": [],
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "total_tokens": 15,
                            },
                        },
                    },
                ]
            )
            with patch.object(controller, "_client", return_value=client):
                messages = list(
                    controller.test_stream(
                        {
                            "mode": "direct",
                            "text": "生成图片",
                            "model": "gpt-test",
                            "reasoning_effort": "low",
                            "image_generation": True,
                        }
                    )
                )

        image_message = next(message for message in messages if message.get("images"))
        self.assertTrue(image_message["images"][0]["data_url"].startswith("data:image/"))
        self.assertIn("generated image base64", image_message["event"]["item"]["result"])
        self.assertEqual(messages[-1]["result"]["generated_images"][0]["id"], "image-stream")

    def test_image_generation_rejects_chat_completions_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = DashboardController(Path(directory) / "settings.json")
            with self.assertRaisesRegex(ValueError, "只支持 Responses"):
                controller.test(
                    {
                        "mode": "local_api",
                        "api_format": "chat",
                        "image_generation": True,
                    }
                )

    def test_dashboard_stream_endpoint_returns_ndjson(self) -> None:
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
                    f"{base_url}/api/test/stream",
                    data=b"{}",
                    method="POST",
                    headers={
                        "Cookie": cookie,
                        "Content-Type": "application/json",
                        "X-Codex-Dashboard": "1",
                    },
                )
                with patch.object(
                    controller,
                    "test_stream",
                    return_value=iter(
                        [
                            {"type": "start", "result": {"text": ""}},
                            {"type": "delta", "delta": "OK"},
                        ]
                    ),
                ):
                    with urllib.request.urlopen(request) as response:
                        events = [json.loads(line) for line in response]
                        content_type = response.headers["Content-Type"]
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertIn("application/x-ndjson", content_type)
        self.assertEqual(events[1]["delta"], "OK")

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
                            "max_output_tokens": 32_000,
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
        self.assertEqual(responses_result["request"]["max_output_tokens"], 32_000)

    def test_local_api_stream_uses_real_sse_endpoint(self) -> None:
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
                upstream_events = [
                    {"type": "response.output_text.delta", "delta": "API"},
                    {"type": "response.output_text.delta", "delta": " 流式"},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp-local-stream",
                            "model": "gpt-test",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [
                                        {"type": "output_text", "text": "API 流式"}
                                    ],
                                }
                            ],
                            "usage": {
                                "input_tokens": 8,
                                "output_tokens": 3,
                                "total_tokens": 11,
                            },
                        },
                    },
                ]
                with patch.object(
                    CodexSubscriptionClient,
                    "iter_response_events",
                    return_value=iter(upstream_events),
                ):
                    messages = list(
                        controller.test_stream(
                            {
                                "mode": "local_api",
                                "api_format": "responses",
                                "text": "测试本地流式",
                                "model": "gpt-test",
                                "reasoning_effort": "low",
                                "max_output_tokens": 128_000,
                            }
                        )
                    )
            finally:
                api_server.shutdown()
                api_server.server_close()
                api_thread.join(timeout=2)

        result = messages[-1]["result"]
        self.assertEqual(result["text"], "API 流式")
        self.assertEqual(result["usage"]["total_tokens"], 11)
        self.assertTrue(result["request"]["stream"])
        self.assertEqual(result["request"]["max_output_tokens"], 128_000)
        self.assertIn("/v1/responses", result["endpoint"])


if __name__ == "__main__":
    unittest.main()
