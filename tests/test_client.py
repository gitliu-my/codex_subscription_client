from __future__ import annotations

import json
import unittest

from codex_subscription.client import CodexSubscriptionClient, parse_response_sse


def event(data: dict[str, object]) -> str:
    return f"data: {json.dumps(data)}"


class ClientTests(unittest.TestCase):
    def test_parse_text_response(self) -> None:
        sse = "\n".join(
            [
                event(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "OK"}],
                        },
                    }
                ),
                event({"type": "response.completed", "response": {"output": []}}),
            ]
        )
        response = parse_response_sse(sse)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.text, "OK")
        self.assertEqual(response.tool_calls, [])

    def test_parse_function_call(self) -> None:
        sse = "\n".join(
            [
                event(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "local_time",
                            "arguments": '{"timezone":"Asia/Shanghai"}',
                        },
                    }
                ),
                event({"type": "response.completed", "response": {"output": []}}),
            ]
        )
        response = parse_response_sse(sse)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.tool_calls[0].name, "local_time")
        self.assertEqual(response.tool_calls[0].arguments["timezone"], "Asia/Shanghai")

    def test_payload_contains_required_subscription_fields(self) -> None:
        client = CodexSubscriptionClient(model="test-model", allow_login=False)
        payload = client._build_payload([], None, None, None)
        self.assertEqual(payload["model"], "test-model")
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertIn("reasoning.encrypted_content", payload["include"])


if __name__ == "__main__":
    unittest.main()
