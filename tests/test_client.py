from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from codex_subscription.client import (
    CodexSubscriptionClient,
    image_to_url,
    parse_response_sse,
)


TOKEN_PAYLOAD = "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjb3VudC0xMjMifX0"
TEST_TOKEN = f"header.{TOKEN_PAYLOAD}.signature"


class FakeAuth:
    def get_access_token(self, allow_login: bool = True) -> str:
        return TEST_TOKEN


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


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
        client = CodexSubscriptionClient(
            model="test-model", reasoning_effort="medium", allow_login=False
        )
        payload = client._build_payload([], None, None, None)
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["reasoning"]["effort"], "medium")
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertIn("reasoning.encrypted_content", payload["include"])
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertFalse(payload["parallel_tool_calls"])

    def test_profile_and_models_are_self_contained(self) -> None:
        client = CodexSubscriptionClient(auth=FakeAuth(), allow_login=False)  # type: ignore[arg-type]
        response = {
            "models": [
                {
                    "slug": "gpt-5.6-luna",
                    "display_name": "GPT-5.6-Luna",
                    "description": "Fast model",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [
                        {"effort": "low"},
                        {"effort": "medium"},
                    ],
                    "input_modalities": ["text", "image"],
                }
            ]
        }
        with patch.object(client, "_read_request", return_value=json.dumps(response).encode()):
            models = client.list_models()

        self.assertEqual(client.client_profile.client_version, "0.144.0")
        self.assertIn("0.144.0", client.client_profile.user_agent)
        self.assertEqual(models[0].slug, "gpt-5.6-luna")
        self.assertEqual(models[0].input_modalities, ("text", "image"))

    def test_image_to_url_encodes_local_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pixel.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            result = image_to_url(path)

        self.assertTrue(result.startswith("data:image/png;base64,"))

    def test_transport_error_is_retried(self) -> None:
        client = CodexSubscriptionClient(allow_login=False)
        request = urllib.request.Request("https://example.com")
        with (
            patch(
                "codex_subscription.client.urllib.request.urlopen",
                side_effect=[
                    urllib.error.URLError("temporary TLS failure"),
                    FakeHttpResponse(b"OK"),
                ],
            ) as urlopen,
            patch("codex_subscription.client.time.sleep") as sleep,
        ):
            result = client._read_request(request)

        self.assertEqual(result, b"OK")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
