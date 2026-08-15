from __future__ import annotations

import json
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from codex_subscription.api_keys import ApiKeyStore, MemorySecretStore
from codex_subscription.client import (
    CodexBackendError,
    CodexResponse,
    CodexSubscriptionClient,
    SubscriptionModel,
    ToolCall,
)
from codex_subscription.server import (
    SubscriptionApi,
    SubscriptionApiServer,
    _chat_completion_body,
    _chat_messages_to_input,
    _chat_sse_event_stream,
    _normalize_chat_tools,
    _normalize_responses_input,
    _responses_body,
)


class _NoopClient:
    def list_models(self):
        return []


class _BlockingModelsClient:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def list_models(self):
        self.started.set()
        self.release.wait(timeout=2)
        return []


class _PolicyClient:
    model = "gpt-5.6-luna"
    reasoning_effort = "low"

    def list_models(self):
        return [
            SubscriptionModel(
                slug=slug,
                display_name=slug,
                description="",
                default_reasoning_effort="low",
                supported_reasoning_efforts=("low", "medium"),
                input_modalities=("text",),
            )
            for slug in ("gpt-5.6-luna", "gpt-5.6-sol")
        ]


class ServerTests(unittest.TestCase):
    def test_application_keys_are_independent_from_control_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keys = ApiKeyStore(
                Path(directory) / "api_keys.db", MemorySecretStore()
            )
            control_key = "control-local-api-key-1234567890"
            keys.ensure_legacy_key(control_key)
            app_record, app_key = keys.create("agent-a")
            server = SubscriptionApiServer(
                ("127.0.0.1", 0),
                _NoopClient(),
                api_key=control_key,
                api_keys=keys,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                request = urllib.request.Request(
                    f"{base_url}/v1/models",
                    headers={"Authorization": f"Bearer {app_key}"},
                )
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)

                control_request = urllib.request.Request(
                    f"{base_url}/__csub/status",
                    headers={"Authorization": f"Bearer {app_key}"},
                )
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(control_request)
                self.assertEqual(context.exception.code, 401)
                context.exception.close()

                keys.set_enabled(app_record.id, False)
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                self.assertEqual(context.exception.code, 401)
                context.exception.close()
                self.assertEqual(keys.get(app_record.id).request_count, 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_application_key_policy_filters_models_and_rejects_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            keys = ApiKeyStore(
                Path(directory) / "api_keys.db", MemorySecretStore()
            )
            _, app_key = keys.create(
                "restricted-agent", {"gpt-5.6-luna": ["low"]}
            )
            server = SubscriptionApiServer(
                ("127.0.0.1", 0),
                _PolicyClient(),
                api_key="control-local-api-key-1234567890",
                api_keys=keys,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                models_request = urllib.request.Request(
                    f"{base_url}/v1/models",
                    headers={"Authorization": f"Bearer {app_key}"},
                )
                with urllib.request.urlopen(models_request) as response:
                    models = json.load(response)
                self.assertEqual(
                    [item["id"] for item in models["data"]],
                    ["gpt-5.6-luna"],
                )

                denied_requests = (
                    (
                        "/v1/responses",
                        {
                            "model": "gpt-5.6-sol",
                            "reasoning": {"effort": "low"},
                            "input": "hello",
                        },
                        "model gpt-5.6-sol",
                    ),
                    (
                        "/v1/chat/completions",
                        {
                            "model": "gpt-5.6-luna",
                            "reasoning_effort": "medium",
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                        "reasoning effort medium",
                    ),
                )
                for path, body, message in denied_requests:
                    request = urllib.request.Request(
                        f"{base_url}{path}",
                        data=json.dumps(body).encode(),
                        headers={
                            "Authorization": f"Bearer {app_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    with self.assertRaises(urllib.error.HTTPError) as context:
                        urllib.request.urlopen(request)
                    self.assertEqual(context.exception.code, 403)
                    error = json.loads(context.exception.read())
                    self.assertIn(message, error["error"]["message"])
                    context.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

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
            usage={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        )
        responses_body = _responses_body(response, "gpt-5.6-luna")
        chat_body = _chat_completion_body(response, "gpt-5.6-luna")

        self.assertEqual(responses_body["output_text"], "OK")
        self.assertEqual(chat_body["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(
            chat_body["choices"][0]["message"]["tool_calls"][0]["function"]["name"],
            "local_time",
        )
        self.assertEqual(responses_body["usage"]["total_tokens"], 7)
        self.assertEqual(chat_body["usage"]["prompt_tokens"], 5)

    def test_responses_shape_preserves_generated_image_output(self) -> None:
        result = CodexResponse(
            text="图片如下",
            output_items=[
                {
                    "type": "image_generation_call",
                    "id": "image-1",
                    "result": "aW1hZ2U=",
                }
            ],
            response_id="resp-image",
            model="gpt-test",
        )

        response = _responses_body(result, "gpt-test")

        self.assertEqual(response["output"][0]["type"], "image_generation_call")
        self.assertEqual(response["output"][0]["result"], "aW1hZ2U=")

    def test_responses_parameters_are_translated_for_upstream(self) -> None:
        api = SubscriptionApi(CodexSubscriptionClient(allow_login=False))
        with patch.object(
            CodexSubscriptionClient,
            "create_response",
            return_value=CodexResponse(text="OK"),
        ) as create_response:
            api.responses(
                {
                    "model": "gpt-test",
                    "input": "hello",
                    "stream": False,
                    "max_output_tokens": 200,
                    "tool_choice": "required",
                    "parallel_tool_calls": False,
                    "reasoning": {"effort": "high", "summary": "detailed"},
                }
            )

        extra = create_response.call_args.kwargs["extra_body"]
        self.assertNotIn("max_output_tokens", extra)
        self.assertEqual(extra["tool_choice"], "required")
        self.assertFalse(extra["parallel_tool_calls"])
        self.assertEqual(extra["reasoning"]["summary"], "detailed")
        self.assertNotIn("stream", extra)

    def test_dsh_like_responses_request_succeeds_through_fake_backend(self) -> None:
        captured_payloads: list[dict[str, object]] = []
        output = [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "checked"}],
            },
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "lookup",
                "arguments": '{"query":"csub"}',
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "OK",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.com/source",
                                "title": "Source",
                            }
                        ],
                    }
                ],
            },
            {
                "type": "image_generation_call",
                "id": "image-1",
                "result": "aW1hZ2U=",
            },
        ]

        def fake_backend(client, payload):
            captured_payloads.append(payload)
            yield {
                "type": "response.completed",
                "response": {
                    "id": "resp-dsh",
                    "model": "gpt-test",
                    "output": output,
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 4,
                        "total_tokens": 16,
                    },
                },
            }

        server = SubscriptionApiServer(
            ("127.0.0.1", 0),
            CodexSubscriptionClient(allow_login=False),
            api_key="d" * 32,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = {
                "model": "gpt-test",
                "input": "hello",
                "instructions": "answer briefly",
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup",
                        "parameters": {"type": "object"},
                    }
                ],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "reasoning": {"effort": "medium", "summary": "detailed"},
                "text": {"verbosity": "low"},
                "include": ["file_search_call.results"],
                "store": False,
                "service_tier": "auto",
                "prompt_cache_key": "dsh-session-1",
                "prompt_cache_retention": "24h",
                "prompt_cache_options": {"mode": "explicit"},
                "max_output_tokens": 32_000,
            }
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/responses",
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": "Bearer " + "d" * 32,
                    "Content-Type": "application/json",
                },
            )
            with patch.object(
                CodexSubscriptionClient, "_stream_authenticated", fake_backend
            ):
                with urllib.request.urlopen(request) as response:
                    result = json.load(response)
                    ignored = response.headers["X-Csub-Ignored-Request-Fields"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn("max_output_tokens", ignored)
        self.assertIn("prompt_cache_options", ignored)
        self.assertIn("prompt_cache_retention", ignored)
        self.assertEqual(result["id"], "resp-dsh")
        self.assertEqual(result["usage"]["total_tokens"], 16)
        self.assertEqual(result["output"][0]["type"], "reasoning")
        self.assertEqual(result["output"][1]["type"], "function_call")
        self.assertEqual(
            result["output"][2]["content"][0]["annotations"][0]["type"],
            "url_citation",
        )
        self.assertEqual(result["output"][3]["type"], "image_generation_call")
        payload = captured_payloads[0]
        self.assertNotIn("max_output_tokens", payload)
        self.assertNotIn("prompt_cache_retention", payload)
        self.assertNotIn("prompt_cache_options", payload)
        self.assertEqual(payload["prompt_cache_key"], "dsh-session-1")
        self.assertEqual(payload["service_tier"], "auto")
        self.assertEqual(payload["reasoning"]["summary"], "detailed")
        self.assertEqual(payload["text"]["verbosity"], "low")
        self.assertEqual(
            payload["include"],
            ["file_search_call.results", "reasoning.encrypted_content"],
        )
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])

    def test_dsh_like_stream_request_ignores_max_output_tokens(self) -> None:
        captured_payloads: list[dict[str, object]] = []

        def fake_backend(client, payload):
            captured_payloads.append(payload)
            yield {"type": "response.created", "response": {"id": "resp-stream"}}
            yield {"type": "response.output_text.delta", "delta": "OK"}
            yield {
                "type": "response.completed",
                "response": {
                    "id": "resp-stream",
                    "output": [],
                    "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                },
            }

        server = SubscriptionApiServer(
            ("127.0.0.1", 0),
            CodexSubscriptionClient(allow_login=False),
            api_key="s" * 32,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/responses",
                data=json.dumps(
                    {
                        "model": "gpt-test",
                        "input": "hello",
                        "stream": True,
                        "max_output_tokens": 128_000,
                        "prompt_cache_key": "dsh-stream",
                    }
                ).encode(),
                headers={
                    "Authorization": "Bearer " + "s" * 32,
                    "Content-Type": "application/json",
                },
            )
            with patch.object(
                CodexSubscriptionClient, "_stream_authenticated", fake_backend
            ):
                with urllib.request.urlopen(request) as response:
                    ignored = response.headers["X-Csub-Ignored-Request-Fields"]
                    streamed = response.read().decode()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(ignored, "max_output_tokens")
        self.assertIn("response.output_text.delta", streamed)
        self.assertIn("[DONE]", streamed)
        self.assertNotIn("max_output_tokens", captured_payloads[0])
        self.assertEqual(captured_payloads[0]["prompt_cache_key"], "dsh-stream")

    def test_unknown_responses_field_returns_local_400_without_backend_call(self) -> None:
        backend_called = False

        def fake_backend(client, payload):
            nonlocal backend_called
            backend_called = True
            yield {"type": "response.completed", "response": {"output": []}}

        server = SubscriptionApiServer(
            ("127.0.0.1", 0),
            CodexSubscriptionClient(allow_login=False),
            api_key="u" * 32,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/responses",
                data=b'{"input":"hello","unknown_option":true}',
                headers={
                    "Authorization": "Bearer " + "u" * 32,
                    "Content-Type": "application/json",
                },
            )
            with patch.object(
                CodexSubscriptionClient, "_stream_authenticated", fake_backend
            ):
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                error = json.loads(context.exception.read())
                context.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(context.exception.code, 400)
        self.assertEqual(error["error"]["type"], "invalid_request_error")
        self.assertIn("unknown_option", error["error"]["message"])
        self.assertFalse(backend_called)

    def test_real_backend_failure_remains_502(self) -> None:
        def failing_backend(client, payload):
            raise CodexBackendError("backend unavailable")
            yield

        server = SubscriptionApiServer(
            ("127.0.0.1", 0),
            CodexSubscriptionClient(allow_login=False),
            api_key="f" * 32,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/responses",
                data=b'{"input":"hello"}',
                headers={
                    "Authorization": "Bearer " + "f" * 32,
                    "Content-Type": "application/json",
                },
            )
            with patch.object(
                CodexSubscriptionClient, "_stream_authenticated", failing_backend
            ):
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(request)
                error = json.loads(context.exception.read())
                context.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(context.exception.code, 502)
        self.assertEqual(error["error"]["type"], "codex_subscription_error")
        self.assertIn("backend unavailable", error["error"]["message"])

    def test_chat_stream_converts_text_deltas_and_usage(self) -> None:
        upstream = iter(
            [
                {"type": "response.created", "response": {}},
                {"type": "response.output_text.delta", "delta": "A"},
                {"type": "response.output_text.delta", "delta": "B"},
                {
                    "type": "response.completed",
                    "response": {
                        "usage": {
                            "input_tokens": 4,
                            "output_tokens": 2,
                            "total_tokens": 6,
                        }
                    },
                },
            ]
        )
        chunks = list(_chat_sse_event_stream(upstream, "gpt-test", True))

        text = "".join(
            choice["delta"].get("content", "")
            for chunk in chunks
            for choice in chunk["choices"]
        )
        self.assertEqual(text, "AB")
        self.assertEqual(chunks[-1]["usage"]["total_tokens"], 6)

    def test_chat_stream_converts_tool_argument_deltas(self) -> None:
        upstream = iter(
            [
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "get_weather",
                        "arguments": "",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "delta": '{"city":',
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "delta": '"上海"}',
                },
                {"type": "response.completed", "response": {}},
            ]
        )
        chunks = list(_chat_sse_event_stream(upstream, "gpt-test", False))
        tool_chunks = [
            call
            for chunk in chunks
            for choice in chunk["choices"]
            for call in choice["delta"].get("tool_calls", [])
        ]

        self.assertEqual(tool_chunks[0]["function"]["name"], "get_weather")
        arguments = "".join(
            call["function"].get("arguments", "") for call in tool_chunks
        )
        self.assertEqual(arguments, '{"city":"上海"}')
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

    def test_sse_reaches_client_before_upstream_completion(self) -> None:
        release = threading.Event()

        def events(*args, **kwargs):
            yield {"type": "response.created", "response": {}}
            yield {"type": "response.output_text.delta", "delta": "first"}
            release.wait(timeout=2)
            yield {
                "type": "response.completed",
                "response": {"output": []},
            }

        client = CodexSubscriptionClient(allow_login=False)
        server = SubscriptionApiServer(
            ("127.0.0.1", 0), client, api_key="a" * 32
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/responses",
                data=b'{"model":"gpt-test","input":"hi","stream":true}',
                headers={
                    "Authorization": "Bearer " + "a" * 32,
                    "Content-Type": "application/json",
                },
            )
            with patch.object(CodexSubscriptionClient, "iter_response_events", events):
                with urllib.request.urlopen(request, timeout=2) as response:
                    first = response.readline().decode()
                    self.assertIn("response.created", first)
                    line = response.readline().decode()
                    if not line.strip():
                        line = response.readline().decode()
                    self.assertIn("response.output_text.delta", line)
                    self.assertFalse(release.is_set())
                    release.set()
                    self.assertIn(b"[DONE]", response.read())
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_concurrency_limit_returns_429_after_queue_timeout(self) -> None:
        client = _BlockingModelsClient()
        server = SubscriptionApiServer(
            ("127.0.0.1", 0),
            client,
            api_key="a" * 32,
            max_concurrency=1,
            queue_timeout_seconds=0.05,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        first_done = threading.Event()

        def first_request() -> None:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/models",
                headers={"Authorization": "Bearer " + "a" * 32},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read()
            first_done.set()

        caller = threading.Thread(target=first_request)
        caller.start()
        try:
            self.assertTrue(client.started.wait(timeout=1))
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v1/models",
                headers={"Authorization": "Bearer " + "a" * 32},
            )
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(request, timeout=1)
            self.assertEqual(context.exception.code, 429)
            context.exception.close()
        finally:
            client.release.set()
            caller.join(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertTrue(first_done.is_set())


if __name__ == "__main__":
    unittest.main()
