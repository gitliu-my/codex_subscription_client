from __future__ import annotations

import threading
import unittest
import urllib.error
import urllib.request

from codex_subscription.client import CodexResponse, ToolCall
from codex_subscription.server import (
    SubscriptionApiServer,
    _chat_completion_body,
    _chat_messages_to_input,
    _normalize_chat_tools,
    _normalize_responses_input,
    _responses_body,
)


class _NoopClient:
    def list_models(self):
        return []


class ServerTests(unittest.TestCase):
    def test_server_rejects_non_loopback_bind_and_short_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "only bind"):
            SubscriptionApiServer(("0.0.0.0", 0), _NoopClient())
        with self.assertRaisesRegex(ValueError, "at least 24"):
            SubscriptionApiServer(
                ("127.0.0.1", 0), _NoopClient(), api_key="too-short"
            )

    def test_options_supports_browser_extension_preflight(self) -> None:
        server = SubscriptionApiServer(
            ("127.0.0.1", 0), _NoopClient(), api_key="a" * 32
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                method="OPTIONS",
                headers={"Origin": "chrome-extension://extension-id"},
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(
                    response.headers["Access-Control-Allow-Origin"],
                    "chrome-extension://extension-id",
                )
                self.assertIn(
                    "Authorization", response.headers["Access-Control-Allow-Headers"]
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_web_page_origin_is_rejected(self) -> None:
        server = SubscriptionApiServer(
            ("127.0.0.1", 0), _NoopClient(), api_key="a" * 32
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                method="OPTIONS",
                headers={"Origin": "https://example.invalid"},
            )
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(request)
            self.assertEqual(context.exception.code, 403)
            self.assertIsNone(
                context.exception.headers.get("Access-Control-Allow-Origin")
            )
            context.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_api_key_is_always_required_but_health_is_public(self) -> None:
        server = SubscriptionApiServer(("127.0.0.1", 0), _NoopClient())
        self.assertGreaterEqual(len(server.api_key), 32)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(f"{base_url}/health") as response:
                self.assertEqual(response.status, 200)
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(f"{base_url}/v1/models")
            self.assertEqual(context.exception.code, 401)
            context.exception.close()

            request = urllib.request.Request(
                f"{base_url}/v1/models",
                headers={"Authorization": f"Bearer {server.api_key}"},
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_responses_string_input_is_normalized(self) -> None:
        items = _normalize_responses_input("hello")
        self.assertEqual(items[0]["role"], "user")
        self.assertEqual(items[0]["content"][0]["text"], "hello")

    def test_chat_messages_support_system_text_and_images(self) -> None:
        items, instructions = _chat_messages_to_input(
            [
                {"role": "system", "content": "Be concise."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AA=="},
                        },
                    ],
                },
            ]
        )
        self.assertEqual(instructions, "Be concise.")
        self.assertEqual(items[0]["content"][1]["type"], "input_image")

    def test_chat_tools_are_converted_to_responses_shape(self) -> None:
        tools = _normalize_chat_tools(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "local_time",
                        "description": "Get time",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
        )
        assert tools is not None
        self.assertEqual(tools[0]["name"], "local_time")
        self.assertNotIn("function", tools[0])

    def test_response_shapes_include_text_and_tool_calls(self) -> None:
        response = CodexResponse(
            text="OK",
            tool_calls=[ToolCall("call-1", "local_time", {"timezone": "UTC"})],
            output_items=[],
        )
        responses_body = _responses_body(response, "gpt-5.6-luna")
        chat_body = _chat_completion_body(response, "gpt-5.6-luna")

        self.assertEqual(responses_body["output_text"], "OK")
        self.assertEqual(chat_body["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(
            chat_body["choices"][0]["message"]["tool_calls"][0]["function"]["name"],
            "local_time",
        )


if __name__ == "__main__":
    unittest.main()
