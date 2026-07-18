from __future__ import annotations

import threading
import unittest
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
    def test_options_supports_browser_extension_preflight(self) -> None:
        server = SubscriptionApiServer(("127.0.0.1", 0), _NoopClient())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                method="OPTIONS",
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
                self.assertIn(
                    "Authorization", response.headers["Access-Control-Allow-Headers"]
                )
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
