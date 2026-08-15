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
from codex_subscription.responses_compat import ResponsesCompatibilityError


TOKEN_PAYLOAD = "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjb3VudC0xMjMifX0"
TEST_TOKEN = f"header.{TOKEN_PAYLOAD}.signature"


class FakeAuth:
    def get_access_token(self, allow_login: bool = True) -> str:
        return TEST_TOKEN


class FakeHttpResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.closed = False

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True
        return None

    def read(self) -> bytes:
        return self.body

    def __iter__(self):
        return iter(self.body.splitlines(keepends=True))


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
                event(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp-123",
                            "model": "gpt-5.6-luna",
                            "output": [],
                            "usage": {
                                "input_tokens": 12,
                                "output_tokens": 3,
                                "total_tokens": 15,
                            },
                        },
                    }
                ),
            ]
        )
        response = parse_response_sse(sse)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.text, "OK")
        self.assertEqual(response.tool_calls, [])
        self.assertEqual(response.response_id, "resp-123")
        self.assertEqual(response.model, "gpt-5.6-luna")
        assert response.usage is not None
        self.assertEqual(response.usage["total_tokens"], 15)

    def test_streaming_client_yields_each_upstream_event(self) -> None:
        body = (
            event({"type": "response.created", "response": {}})
            + "\n\n"
            + event({"type": "response.output_text.delta", "delta": "O"})
            + "\n\n"
            + event(
                {
                    "type": "response.completed",
                    "response": {"output": [], "usage": {"total_tokens": 1}},
                }
            )
            + "\n\n"
        ).encode()
        client = CodexSubscriptionClient(
            auth=FakeAuth(), allow_login=False  # type: ignore[arg-type]
        )
        with patch(
            "codex_subscription.client.urlopen", return_value=FakeHttpResponse(body)
        ):
            events = list(client.iter_response_events([]))

        self.assertEqual(
            [item["type"] for item in events],
            [
                "response.created",
                "response.output_text.delta",
                "response.completed",
            ],
        )

    def test_closing_event_iterator_closes_upstream_response(self) -> None:
        body = (
            event({"type": "response.created", "response": {}}) + "\n\n"
        ).encode()
        upstream = FakeHttpResponse(body)
        client = CodexSubscriptionClient(
            auth=FakeAuth(), allow_login=False  # type: ignore[arg-type]
        )
        with patch("codex_subscription.client.urlopen", return_value=upstream):
            stream = client.iter_response_events([])
            self.assertEqual(next(stream)["type"], "response.created")
            stream.close()

        self.assertTrue(upstream.closed)

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

    def test_payload_rejects_untranslated_options_that_could_override_invariants(self) -> None:
        client = CodexSubscriptionClient(allow_login=False)

        with self.assertRaisesRegex(ResponsesCompatibilityError, "Unsafe.*model"):
            client._build_payload([], None, None, {"model": "other-model"})

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
                "codex_subscription.client.urlopen",
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
