from __future__ import annotations

"""Small local OpenAI-compatible HTTP facade for the subscription client."""

import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator

from .api_keys import ApiKeyRecord, ApiKeyStore
from .auth import CodexOAuthError
from .client import CodexBackendError, CodexResponse, CodexSubscriptionClient
from .settings import DEFAULT_MAX_CONCURRENCY


class SubscriptionApi:
    def __init__(self, client: CodexSubscriptionClient) -> None:
        self.client = client

    def models(self, api_key: ApiKeyRecord | None = None) -> dict[str, Any]:
        models = self.client.list_models()
        if api_key is not None and api_key.permissions is not None:
            models = [model for model in models if model.slug in api_key.permissions]
        return {
            "object": "list",
            "data": [
                {
                    "id": model.slug,
                    "object": "model",
                    "created": 0,
                    "owned_by": "openai",
                    "metadata": asdict(model),
                }
                for model in models
            ],
        }

    def responses(self, body: dict[str, Any]) -> dict[str, Any]:
        model, client, input_items, tools, instructions, extra_body = (
            self._responses_request(body)
        )
        result = client.create_response(
            input_items=input_items,
            tools=tools,
            instructions=instructions,
            extra_body=extra_body,
        )
        return _responses_body(result, model)

    def responses_stream(
        self, body: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        _, client, input_items, tools, instructions, extra_body = (
            self._responses_request(body)
        )
        return client.iter_response_events(
            input_items=input_items,
            tools=tools,
            instructions=instructions,
            extra_body=extra_body,
        )

    def _responses_request(
        self, body: dict[str, Any]
    ) -> tuple[
        str,
        CodexSubscriptionClient,
        list[dict[str, Any]],
        list[dict[str, Any]] | None,
        str | None,
        dict[str, Any],
    ]:
        model = _optional_string(body.get("model")) or self.client.model
        reasoning = body.get("reasoning")
        effort = self.client.reasoning_effort
        if isinstance(reasoning, dict):
            effort = _optional_string(reasoning.get("effort")) or effort

        client = self._request_client(model, effort)
        extra_body = {
            key: value
            for key, value in body.items()
            if key
            not in {"model", "input", "tools", "instructions", "stream"}
        }
        return (
            model,
            client,
            _normalize_responses_input(body.get("input")),
            _normalize_responses_tools(body.get("tools")),
            _optional_string(body.get("instructions")),
            extra_body,
        )

    def chat_completions(self, body: dict[str, Any]) -> dict[str, Any]:
        model, client, input_items, tools, instructions = self._chat_request(body)
        result = client.create_response(
            input_items=input_items,
            tools=tools,
            instructions=instructions,
        )
        return _chat_completion_body(result, model)

    def chat_completions_stream(
        self, body: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        model, client, input_items, tools, instructions = self._chat_request(body)
        events = client.iter_response_events(
            input_items=input_items,
            tools=tools,
            instructions=instructions,
        )
        stream_options = body.get("stream_options")
        include_usage = isinstance(stream_options, dict) and (
            stream_options.get("include_usage") is True
        )

        def converted() -> Iterator[dict[str, Any]]:
            try:
                yield from _chat_sse_event_stream(events, model, include_usage)
            finally:
                close = getattr(events, "close", None)
                if callable(close):
                    close()

        return converted()

    def _chat_request(
        self, body: dict[str, Any]
    ) -> tuple[
        str,
        CodexSubscriptionClient,
        list[dict[str, Any]],
        list[dict[str, Any]] | None,
        str | None,
    ]:
        model = _optional_string(body.get("model")) or self.client.model
        effort = self.client.reasoning_effort
        if isinstance(body.get("reasoning_effort"), str):
            effort = body["reasoning_effort"]

        input_items, instructions = _chat_messages_to_input(body.get("messages"))
        client = self._request_client(model, effort)
        return (
            model,
            client,
            input_items,
            _normalize_chat_tools(body.get("tools")),
            instructions,
        )

    def _request_client(self, model: str, effort: str) -> CodexSubscriptionClient:
        return CodexSubscriptionClient(
            model=model,
            reasoning_effort=effort,
            timeout_seconds=self.client.timeout_seconds,
            allow_login=self.client.allow_login,
            auth=self.client.auth,
            backend_url=self.client.backend_url,
            models_url=self.client.models_url,
            client_profile=self.client.client_profile,
        )


class SubscriptionApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        client: CodexSubscriptionClient,
        api_key: str | None = None,
        api_keys: ApiKeyStore | None = None,
        allowed_origins: tuple[str, ...] | None = None,
        max_concurrency: int | None = None,
        queue_timeout_seconds: float = 30,
    ) -> None:
        if address[0] not in {"127.0.0.1", "localhost"}:
            raise ValueError("Local API may only bind to 127.0.0.1 or localhost")
        if api_key is not None and len(api_key) < 24:
            raise ValueError("Local API key must contain at least 24 characters")
        effective_max_concurrency = (
            int(
                os.environ.get(
                    "CODEX_SUBSCRIPTION_MAX_CONCURRENCY",
                    str(DEFAULT_MAX_CONCURRENCY),
                )
            )
            if max_concurrency is None
            else max_concurrency
        )
        if effective_max_concurrency < 1:
            raise ValueError("Max concurrency must be at least 1")
        if queue_timeout_seconds < 0:
            raise ValueError("Queue timeout may not be negative")
        super().__init__(address, SubscriptionApiHandler)
        self.api = SubscriptionApi(client)
        self.api_key = api_key or secrets.token_urlsafe(32)
        self.api_keys = api_keys or _SingleApiKeyAuthenticator(self.api_key)
        self.allowed_origins = allowed_origins or _configured_allowed_origins()
        self.max_concurrency = effective_max_concurrency
        self.queue_timeout_seconds = queue_timeout_seconds
        self.request_slots = threading.BoundedSemaphore(effective_max_concurrency)


class SubscriptionApiHandler(BaseHTTPRequestHandler):
    server: SubscriptionApiServer

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self.headers.get("Origin") and not self._allowed_cors_origin():
            self._error(403, "Origin is not allowed")
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if not self._authorized():
            return
        if self.path == "/__csub/status":
            self._json(200, {"status": "running", "pid": os.getpid()})
            return
        if self.path == "/v1/models":
            self._call(
                lambda: self.server.api.models(
                    getattr(self, "_authenticated_api_key", None)
                )
            )
            return
        self._error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        if self.path == "/__csub/stop":
            self._json(200, {"status": "stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        try:
            body = self._read_json()
        except ValueError as exc:
            self._error(400, str(exc))
            return

        if self.path == "/v1/responses":
            if not self._policy_authorized(body, responses_api=True):
                return
            if body.get("stream") is True:
                self._stream(self.server.api.responses_stream, body)
            else:
                self._call(lambda: self.server.api.responses(body))
            return
        if self.path == "/v1/chat/completions":
            if not self._policy_authorized(body, responses_api=False):
                return
            if body.get("stream") is True:
                self._stream(self.server.api.chat_completions_stream, body)
            else:
                self._call(lambda: self.server.api.chat_completions(body))
            return
        self._error(404, "Not found")

    def _call(
        self,
        operation: Any,
    ) -> None:
        if not self._acquire_request_slot():
            return
        try:
            result = operation()
        except (CodexOAuthError, CodexBackendError, ValueError) as exc:
            upstream_error = isinstance(exc, (CodexOAuthError, CodexBackendError))
            self._error(502 if upstream_error else 400, str(exc))
            return
        else:
            self._json(200, result)
        finally:
            self.server.request_slots.release()

    def _stream(
        self,
        operation: Callable[[dict[str, Any]], Iterator[dict[str, Any]]],
        body: dict[str, Any] | None = None,
    ) -> None:
        if not self._acquire_request_slot():
            return
        events: Iterator[dict[str, Any]] | None = None
        started = False
        try:
            events = operation(body or {})
            first = next(events)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors_headers()
            self.end_headers()
            started = True
            self._write_sse_event(first)
            for event in events:
                self._write_sse_event(event)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except StopIteration:
            if not started:
                self._error(502, "Codex backend returned an empty event stream")
        except (BrokenPipeError, ConnectionResetError):
            return
        except (CodexOAuthError, CodexBackendError, ValueError) as exc:
            if not started:
                upstream_error = isinstance(exc, (CodexOAuthError, CodexBackendError))
                self._error(502 if upstream_error else 400, str(exc))
            else:
                try:
                    self._write_sse_event(
                        {
                            "type": "error",
                            "error": {
                                "message": str(exc),
                                "type": "codex_subscription_error",
                            },
                        }
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            if events is not None:
                close = getattr(events, "close", None)
                if callable(close):
                    close()
            self.server.request_slots.release()

    def _acquire_request_slot(self) -> bool:
        acquired = self.server.request_slots.acquire(
            timeout=self.server.queue_timeout_seconds
        )
        if not acquired:
            self._error(
                429,
                f"Local concurrency limit reached ({self.server.max_concurrency})",
            )
        return acquired

    def _write_sse_event(self, event: dict[str, Any]) -> None:
        payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode(
            "utf-8"
        )
        self.wfile.write(payload)
        self.wfile.flush()

    def _authorized(self) -> bool:
        self._authenticated_api_key = None
        provided = self.headers.get("Authorization", "")
        if provided.startswith("Bearer "):
            token = provided.removeprefix("Bearer ")
            if self.path.startswith("/__csub/"):
                if secrets.compare_digest(token, self.server.api_key):
                    return True
            else:
                record = self.server.api_keys.authenticate(token)
                if record is not None:
                    self._authenticated_api_key = record
                    return True
        self._error(401, "Invalid local API key")
        return False

    def _policy_authorized(
        self,
        body: dict[str, Any],
        *,
        responses_api: bool,
    ) -> bool:
        record = getattr(self, "_authenticated_api_key", None)
        if record is None or record.permissions is None:
            return True
        model, effort = _requested_model_effort(
            body,
            self.server.api.client.model,
            self.server.api.client.reasoning_effort,
            responses_api=responses_api,
        )
        if record.allows(model, effort):
            return True
        if model not in record.permissions:
            message = f"API key is not allowed to access model {model}"
        else:
            message = (
                f"API key is not allowed to use reasoning effort {effort} "
                f"with model {model}"
            )
        self._error(403, message)
        return False

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0 or length > 20 * 1024 * 1024:
            raise ValueError("Request body must be between 1 byte and 20 MiB")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, message: str) -> None:
        self._json(
            status,
            {
                "error": {
                    "message": message,
                    "type": "codex_subscription_error",
                    "code": None,
                }
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _cors_headers(self) -> None:
        origin = self._allowed_cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _allowed_cors_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        if origin in self.server.allowed_origins:
            return origin
        extension_prefixes = (
            "chrome-extension://",
            "moz-extension://",
            "safari-web-extension://",
        )
        if origin.startswith(extension_prefixes):
            remainder = origin.split("://", 1)[1]
            if remainder and all(character not in remainder for character in "/?#"):
                return origin
        return None


def serve(
    host: str = "127.0.0.1",
    port: int = 8317,
    api_key: str | None = None,
    client: CodexSubscriptionClient | None = None,
    show_api_key: bool = True,
    max_concurrency: int | None = None,
) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Local API may only bind to 127.0.0.1 or localhost")
    effective_api_key = (
        api_key or os.environ.get("CODEX_SUBSCRIPTION_API_KEY") or secrets.token_urlsafe(32)
    )
    api_keys = ApiKeyStore()
    api_keys.ensure_legacy_key(effective_api_key)
    server = SubscriptionApiServer(
        (host, port),
        client or CodexSubscriptionClient(),
        api_key=effective_api_key,
        api_keys=api_keys,
        max_concurrency=max_concurrency,
    )
    print(f"Codex subscription API listening on http://{host}:{port}")
    if show_api_key:
        print(f"Local API key: {effective_api_key}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _configured_allowed_origins() -> tuple[str, ...]:
    value = os.environ.get("CODEX_SUBSCRIPTION_ALLOWED_ORIGINS", "")
    return tuple(origin.strip() for origin in value.split(",") if origin.strip())


class _SingleApiKeyAuthenticator:
    """Compatibility adapter for embedded servers that pass one key directly."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def authenticate(self, secret: str) -> ApiKeyRecord | None:
        if not secrets.compare_digest(secret, self.api_key):
            return None
        return ApiKeyRecord(
            id="legacy",
            name="Legacy API Key",
            prefix="legacy",
            enabled=True,
            is_system=True,
            permissions=None,
            created_at="",
            updated_at="",
            last_used_at=None,
            request_count=0,
        )


def _requested_model_effort(
    body: dict[str, Any],
    default_model: str,
    default_effort: str,
    *,
    responses_api: bool,
) -> tuple[str, str]:
    model = _optional_string(body.get("model")) or default_model
    effort = default_effort
    if responses_api:
        reasoning = body.get("reasoning")
        if isinstance(reasoning, dict):
            effort = _optional_string(reasoning.get("effort")) or effort
    else:
        effort = _optional_string(body.get("reasoning_effort")) or effort
    return model, effort


def _normalize_responses_input(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": value}],
            }
        ]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise ValueError("input must be a string or an array of Responses input items")


def _normalize_responses_tools(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise ValueError("tools must be an array")


def _normalize_chat_tools(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("tools must be an array")
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise ValueError("only function tools are supported")
        function = item.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ValueError("function tool is missing function.name")
        normalized.append(
            {
                "type": "function",
                "name": function["name"],
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {"type": "object"}),
            }
        )
    return normalized


def _chat_messages_to_input(value: Any) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(value, list):
        raise ValueError("messages must be an array")
    input_items: list[dict[str, Any]] = []
    instruction_parts: list[str] = []
    for message in value:
        if not isinstance(message, dict):
            raise ValueError("each message must be an object")
        role = message.get("role")
        if role in {"system", "developer"}:
            text = _message_text(message.get("content"))
            if text:
                instruction_parts.append(text)
            continue
        if role == "tool":
            call_id = _optional_string(message.get("tool_call_id"))
            if not call_id:
                raise ValueError("tool message is missing tool_call_id")
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _message_text(message.get("content")),
                }
            )
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"unsupported message role: {role}")
        content = _chat_content(message.get("content"), role)
        if content:
            input_items.append({"type": "message", "role": role, "content": content})
        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
    return input_items, "\n\n".join(instruction_parts) or None


def _chat_content(value: Any, role: str) -> list[dict[str, Any]]:
    text_type = "output_text" if role == "assistant" else "input_text"
    if isinstance(value, str):
        return [{"type": text_type, "text": value}]
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("message content must be text or an array")
    content: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("message content item must be an object")
        if item.get("type") == "text":
            content.append({"type": text_type, "text": str(item.get("text") or "")})
        elif item.get("type") == "image_url" and role == "user":
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
                detail = image_url.get("detail", "auto")
            else:
                url = image_url
                detail = "auto"
            if not isinstance(url, str):
                raise ValueError("image_url content is missing a URL")
            content.append({"type": "input_image", "image_url": url, "detail": detail})
        else:
            raise ValueError(f"unsupported message content type: {item.get('type')}")
    return content


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text") or "")
            for item in value
            if isinstance(item, dict) and item.get("type") == "text"
        )
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _responses_body(result: CodexResponse, model: str) -> dict[str, Any]:
    return {
        "id": result.response_id or f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": result.output_items,
        "output_text": result.text,
        "error": None,
        "incomplete_details": None,
        "usage": result.usage,
    }


def _chat_completion_body(result: CodexResponse, model: str) -> dict[str, Any]:
    tool_calls = [
        {
            "id": call.call_id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }
        for call in result.tool_calls
    ]
    message: dict[str, Any] = {"role": "assistant", "content": result.text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": _chat_usage(result.usage),
    }


def _chat_sse_event_stream(
    events: Iterator[dict[str, Any]], model: str, include_usage: bool
) -> Iterator[dict[str, Any]]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    base = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
    }
    yield _chat_stream_chunk(base, {"role": "assistant"})

    tool_indexes: dict[str, int] = {}
    streamed_arguments: set[int] = set()
    completed = False
    had_tool_calls = False

    for event in events:
        event_type = event.get("type")
        if event_type == "response.output_text.delta" and isinstance(
            event.get("delta"), str
        ):
            yield _chat_stream_chunk(base, {"content": event["delta"]})
            continue

        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            had_tool_calls = True
            call_id = str(item.get("call_id") or item.get("id") or "")
            output_index = event.get("output_index")
            keys = [call_id] if call_id else []
            if isinstance(output_index, int):
                keys.append(f"output:{output_index}")
            index = next(
                (tool_indexes[key] for key in keys if key in tool_indexes),
                len(set(tool_indexes.values())),
            )
            is_new = all(key not in tool_indexes for key in keys)
            for key in keys:
                tool_indexes[key] = index
            if is_new:
                yield _chat_stream_chunk(
                    base,
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": str(item.get("name") or ""),
                                    "arguments": "",
                                },
                            }
                        ]
                    },
                )
            arguments = item.get("arguments")
            if (
                event_type == "response.output_item.done"
                and isinstance(arguments, str)
                and arguments
                and index not in streamed_arguments
            ):
                yield _chat_tool_arguments_chunk(base, index, arguments)
                streamed_arguments.add(index)
            continue

        if event_type == "response.function_call_arguments.delta" and isinstance(
            event.get("delta"), str
        ):
            had_tool_calls = True
            output_index = event.get("output_index")
            key = f"output:{output_index}" if isinstance(output_index, int) else ""
            index = tool_indexes.get(key, len(set(tool_indexes.values())))
            if key:
                tool_indexes[key] = index
            yield _chat_tool_arguments_chunk(base, index, event["delta"])
            streamed_arguments.add(index)
            continue

        if event_type in {"response.completed", "response.done"}:
            response = event.get("response")
            usage = response.get("usage") if isinstance(response, dict) else None
            yield _chat_stream_chunk(
                base,
                {},
                finish_reason="tool_calls" if had_tool_calls else "stop",
            )
            if include_usage and isinstance(usage, dict):
                usage_chunk = dict(base)
                usage_chunk["choices"] = []
                usage_chunk["usage"] = _chat_usage(usage)
                yield usage_chunk
            completed = True

    if not completed:
        yield _chat_stream_chunk(
            base,
            {},
            finish_reason="tool_calls" if had_tool_calls else "stop",
        )


def _chat_stream_chunk(
    base: dict[str, Any],
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    chunk = dict(base)
    chunk["choices"] = [
        {"index": 0, "delta": delta, "finish_reason": finish_reason}
    ]
    return chunk


def _chat_tool_arguments_chunk(
    base: dict[str, Any], index: int, arguments: str
) -> dict[str, Any]:
    return _chat_stream_chunk(
        base,
        {
            "tool_calls": [
                {"index": index, "function": {"arguments": arguments}}
            ]
        },
    )


def _chat_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    result: dict[str, Any] = {
        "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
        "completion_tokens": usage.get(
            "output_tokens", usage.get("completion_tokens", 0)
        ),
        "total_tokens": usage.get("total_tokens", 0),
    }
    if isinstance(usage.get("input_tokens_details"), dict):
        result["prompt_tokens_details"] = usage["input_tokens_details"]
    if isinstance(usage.get("output_tokens_details"), dict):
        result["completion_tokens_details"] = usage["output_tokens_details"]
    return result


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
