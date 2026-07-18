from __future__ import annotations

"""Small local OpenAI-compatible HTTP facade for the subscription client."""

import json
import os
import secrets
import time
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .auth import CodexOAuthError
from .client import CodexBackendError, CodexResponse, CodexSubscriptionClient


class SubscriptionApi:
    def __init__(self, client: CodexSubscriptionClient) -> None:
        self.client = client

    def models(self) -> dict[str, Any]:
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
                for model in self.client.list_models()
            ],
        }

    def responses(self, body: dict[str, Any]) -> dict[str, Any]:
        model = _optional_string(body.get("model")) or self.client.model
        reasoning = body.get("reasoning")
        effort = self.client.reasoning_effort
        if isinstance(reasoning, dict):
            effort = _optional_string(reasoning.get("effort")) or effort

        client = self._request_client(model, effort)
        result = client.create_response(
            input_items=_normalize_responses_input(body.get("input")),
            tools=_normalize_responses_tools(body.get("tools")),
            instructions=_optional_string(body.get("instructions")),
        )
        return _responses_body(result, model)

    def chat_completions(self, body: dict[str, Any]) -> dict[str, Any]:
        model = _optional_string(body.get("model")) or self.client.model
        effort = self.client.reasoning_effort
        if isinstance(body.get("reasoning_effort"), str):
            effort = body["reasoning_effort"]

        input_items, instructions = _chat_messages_to_input(body.get("messages"))
        client = self._request_client(model, effort)
        result = client.create_response(
            input_items=input_items,
            tools=_normalize_chat_tools(body.get("tools")),
            instructions=instructions,
        )
        return _chat_completion_body(result, model)

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
        allowed_origins: tuple[str, ...] | None = None,
    ) -> None:
        if address[0] not in {"127.0.0.1", "localhost"}:
            raise ValueError("Local API may only bind to 127.0.0.1 or localhost")
        if api_key is not None and len(api_key) < 24:
            raise ValueError("Local API key must contain at least 24 characters")
        super().__init__(address, SubscriptionApiHandler)
        self.api = SubscriptionApi(client)
        self.api_key = api_key or secrets.token_urlsafe(32)
        self.allowed_origins = allowed_origins or _configured_allowed_origins()


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
        if self.path == "/v1/models":
            self._call(self.server.api.models)
            return
        self._error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        try:
            body = self._read_json()
        except ValueError as exc:
            self._error(400, str(exc))
            return

        if self.path == "/v1/responses":
            self._call(
                lambda: self.server.api.responses(body),
                stream=body.get("stream") is True,
                stream_kind="responses",
            )
            return
        if self.path == "/v1/chat/completions":
            self._call(
                lambda: self.server.api.chat_completions(body),
                stream=body.get("stream") is True,
                stream_kind="chat",
            )
            return
        self._error(404, "Not found")

    def _call(
        self,
        operation: Any,
        stream: bool = False,
        stream_kind: str | None = None,
    ) -> None:
        try:
            result = operation()
        except (CodexOAuthError, CodexBackendError, ValueError) as exc:
            upstream_error = isinstance(exc, (CodexOAuthError, CodexBackendError))
            self._error(502 if upstream_error else 400, str(exc))
            return
        if stream:
            self._sse(result, stream_kind or "responses")
        else:
            self._json(200, result)

    def _authorized(self) -> bool:
        expected = self.server.api_key
        provided = self.headers.get("Authorization", "")
        if provided.startswith("Bearer ") and secrets.compare_digest(
            provided.removeprefix("Bearer "), expected
        ):
            return True
        self._error(401, "Invalid local API key")
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

    def _sse(self, result: dict[str, Any], kind: str) -> None:
        if kind == "chat":
            events = _chat_sse_events(result)
        else:
            events = _responses_sse_events(result)
        payload = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        payload += "data: [DONE]\n\n"
        encoded = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self._cors_headers()
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

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
) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Local API may only bind to 127.0.0.1 or localhost")
    effective_api_key = (
        api_key or os.environ.get("CODEX_SUBSCRIPTION_API_KEY") or secrets.token_urlsafe(32)
    )
    server = SubscriptionApiServer(
        (host, port), client or CodexSubscriptionClient(), api_key=effective_api_key
    )
    print(f"Codex subscription API listening on http://{host}:{port}")
    print(f"Local API key: {effective_api_key}")
    server.serve_forever()


def _configured_allowed_origins() -> tuple[str, ...]:
    value = os.environ.get("CODEX_SUBSCRIPTION_ALLOWED_ORIGINS", "")
    return tuple(origin.strip() for origin in value.split(",") if origin.strip())


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
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": result.output_items,
        "output_text": result.text,
        "error": None,
        "incomplete_details": None,
        "usage": None,
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
        "usage": None,
    }


def _responses_sse_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    created = dict(result)
    created["status"] = "in_progress"
    created["output"] = []
    events: list[dict[str, Any]] = [
        {"type": "response.created", "sequence_number": 0, "response": created}
    ]
    if result.get("output_text"):
        events.append(
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "output_index": 0,
                "content_index": 0,
                "delta": result["output_text"],
            }
        )
    events.append(
        {
            "type": "response.completed",
            "sequence_number": len(events),
            "response": result,
        }
    )
    return events


def _chat_sse_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    choice = result["choices"][0]
    message = choice["message"]
    base = {
        "id": result["id"],
        "object": "chat.completion.chunk",
        "created": result["created"],
        "model": result["model"],
    }
    first = dict(base)
    first["choices"] = [
        {
            "index": 0,
            "delta": {key: value for key, value in message.items() if value is not None},
            "finish_reason": None,
        }
    ]
    last = dict(base)
    last["choices"] = [
        {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
    ]
    return [first, last]


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
