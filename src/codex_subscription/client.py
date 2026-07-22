from __future__ import annotations

"""Direct ChatGPT Codex backend client using subscription OAuth tokens."""

import base64
import json
import mimetypes
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence, TypeVar

from .auth import CodexOAuth, CodexOAuthError, extract_chatgpt_account_id
from .transport import urlopen


DEFAULT_BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"


@dataclass(frozen=True)
class CodexClientProfile:
    """Self-contained Codex protocol identity; no local Codex install is required."""

    client_version: str = "0.144.0"
    user_agent: str = (
        "codex-tui/0.144.0 (Mac OS 26.5.1; arm64) "
        "iTerm.app/3.6.11 (codex-tui; 0.144.0)"
    )
    originator: str = "codex-tui"


DEFAULT_CLIENT_PROFILE = CodexClientProfile()


class CodexBackendError(RuntimeError):
    """Raised when the Codex backend rejects or cannot complete a request."""


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class CodexResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    output_items: list[dict[str, Any]] = field(default_factory=list)
    response_id: str | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None

    def require_text(self) -> str:
        if self.tool_calls:
            names = ", ".join(call.name for call in self.tool_calls)
            raise CodexBackendError(f"模型返回了工具调用而不是最终文本：{names}")
        if not self.text:
            raise CodexBackendError("模型没有返回文本。")
        return self.text


@dataclass(frozen=True)
class SubscriptionModel:
    slug: str
    display_name: str
    description: str
    default_reasoning_effort: str | None
    supported_reasoning_efforts: tuple[str, ...]
    input_modalities: tuple[str, ...]


class CodexSubscriptionClient:
    """Text and structured Responses client backed by ChatGPT subscription OAuth."""

    def __init__(
        self,
        model: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: int | None = None,
        allow_login: bool | None = None,
        auth: CodexOAuth | None = None,
        backend_url: str = DEFAULT_BACKEND_URL,
        models_url: str = DEFAULT_MODELS_URL,
        client_profile: CodexClientProfile = DEFAULT_CLIENT_PROFILE,
    ) -> None:
        self.model = model or os.getenv("CODEX_SUBSCRIPTION_MODEL", "gpt-5.6-luna")
        self.reasoning_effort = reasoning_effort or os.getenv(
            "CODEX_SUBSCRIPTION_REASONING_EFFORT", "medium"
        )
        self.timeout_seconds = timeout_seconds or int(
            os.getenv("CODEX_SUBSCRIPTION_TIMEOUT_SECONDS", "180")
        )
        if allow_login is None:
            allow_login = os.getenv("CODEX_SUBSCRIPTION_AUTO_LOGIN", "1") != "0"
        self.allow_login = allow_login
        self.auth = auth or CodexOAuth()
        self.backend_url = backend_url
        self.models_url = models_url
        self.client_profile = client_profile

    def generate(
        self,
        prompt: str,
        instructions: str | None = None,
        images: Sequence[str | Path] | None = None,
    ) -> str:
        return self.generate_response(prompt, instructions, images).require_text()

    def generate_response(
        self,
        prompt: str,
        instructions: str | None = None,
        images: Sequence[str | Path] | None = None,
    ) -> CodexResponse:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for image in images or ():
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_to_url(image),
                    "detail": "auto",
                }
            )
        return self.create_response(
            input_items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            ],
            instructions=instructions,
        )

    def create_response(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        instructions: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> CodexResponse:
        parsed = parse_response_events(
            self.iter_response_events(input_items, tools, instructions, extra_body)
        )
        if parsed is None:
            raise CodexBackendError("Codex backend 没有返回完整 response.completed 事件。")
        return parsed

    def iter_response_events(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        instructions: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield upstream Responses SSE events as soon as they arrive."""

        payload = self._build_payload(input_items, tools, instructions, extra_body)
        yield from self._stream_authenticated(payload)

    def list_models(self) -> list[SubscriptionModel]:
        """Return models currently exposed to this account and client profile."""

        return self._run_authenticated(self._send_models)

    def _run_authenticated(self, operation: Callable[[str], "T"]) -> "T":
        access_token = self.auth.get_access_token(allow_login=self.allow_login)
        try:
            return operation(access_token)
        except _UnauthorizedError:
            try:
                access_token = self.auth.refresh_after_unauthorized(
                    access_token
                ).access_token
            except CodexOAuthError:
                if not self.allow_login:
                    raise
                access_token = self.auth.login().access_token
            try:
                return operation(access_token)
            except _UnauthorizedError as exc:
                raise CodexBackendError("重新认证后 Codex backend 仍然返回 401。") from exc

    def _stream_authenticated(
        self, payload: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        access_token = self.auth.get_access_token(allow_login=self.allow_login)
        try:
            yield from self._send_events(payload, access_token)
            return
        except _UnauthorizedError:
            try:
                access_token = self.auth.refresh_after_unauthorized(
                    access_token
                ).access_token
            except CodexOAuthError:
                if not self.allow_login:
                    raise
                access_token = self.auth.login().access_token
        try:
            yield from self._send_events(payload, access_token)
        except _UnauthorizedError as exc:
            raise CodexBackendError("重新认证后 Codex backend 仍然返回 401。") from exc

    def _send_events(
        self, payload: dict[str, Any], access_token: str
    ) -> Iterator[dict[str, Any]]:
        account_id = extract_chatgpt_account_id(access_token)
        request = urllib.request.Request(
            self.backend_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(access_token, account_id, "text/event-stream"),
            method="POST",
        )
        response = self._open_request(request)
        try:
            with response:
                yield from _iter_sse_events(response)
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            raise CodexBackendError(f"Codex backend 流式请求失败：{exc}") from exc

    def _send_models(self, access_token: str) -> list[SubscriptionModel]:
        account_id = extract_chatgpt_account_id(access_token)
        query = urllib.parse.urlencode(
            {"client_version": self.client_profile.client_version}
        )
        request = urllib.request.Request(
            f"{self.models_url}?{query}",
            headers=self._headers(access_token, account_id, "application/json"),
            method="GET",
        )
        try:
            body = json.loads(self._read_request(request).decode("utf-8"))
            if not isinstance(body, dict):
                raise CodexBackendError("Codex models 接口返回值不是 JSON object。")
            raw_models = body.get("models", [])
            if not isinstance(raw_models, list):
                raise CodexBackendError("Codex models 接口缺少 models 数组。")
            return [
                _parse_subscription_model(model)
                for model in raw_models
                if isinstance(model, dict) and model.get("slug")
            ]
        except json.JSONDecodeError as exc:
            raise CodexBackendError("Codex models 接口返回了无效 JSON。") from exc

    def _headers(
        self, access_token: str, account_id: str, accept: str
    ) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": accept,
            "Authorization": f"Bearer {access_token}",
            "Chatgpt-Account-Id": account_id,
            "Originator": self.client_profile.originator,
            "User-Agent": self.client_profile.user_agent,
            "Connection": "Keep-Alive",
        }

    def _read_request(self, request: urllib.request.Request) -> bytes:
        with self._open_request(request) as response:
            return response.read()

    def _open_request(self, request: urllib.request.Request) -> Any:
        attempts = 3
        for attempt in range(attempts):
            try:
                return urlopen(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 401:
                    raise _UnauthorizedError(detail) from exc
                raise CodexBackendError(
                    f"Codex backend 返回 HTTP {exc.code}：{detail}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.25 * (2**attempt))
                    continue
                raise CodexBackendError(f"Codex backend 请求失败：{exc}") from exc
        raise AssertionError("unreachable")

    def _build_payload(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        instructions: str | None,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions or "直接回答用户。不要自行运行命令或修改文件。",
            "input": input_items,
            "tools": tools or [],
            "tool_choice": "auto",
            "parallel_tool_calls": bool(tools),
            "store": False,
            "stream": True,
            "reasoning": {"effort": self.reasoning_effort, "summary": "auto"},
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
        }
        if extra_body:
            payload.update(extra_body)
        return payload


class _UnauthorizedError(RuntimeError):
    pass


T = TypeVar("T")


def image_to_url(image: str | Path) -> str:
    value = str(image)
    if value.startswith(("https://", "http://", "data:")):
        return value

    path = Path(value).expanduser()
    if not path.is_file():
        raise CodexBackendError(f"图片文件不存在：{path}")
    mime_type = mimetypes.guess_type(path.name)[0]
    if not mime_type or not mime_type.startswith("image/"):
        raise CodexBackendError(f"无法识别图片类型：{path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _parse_subscription_model(model: dict[str, Any]) -> SubscriptionModel:
    reasoning_levels = model.get("supported_reasoning_levels")
    efforts: list[str] = []
    if isinstance(reasoning_levels, list):
        for level in reasoning_levels:
            if isinstance(level, dict) and isinstance(level.get("effort"), str):
                efforts.append(level["effort"])

    modalities = model.get("input_modalities")
    if not isinstance(modalities, list):
        modalities = []
    return SubscriptionModel(
        slug=str(model["slug"]),
        display_name=str(model.get("display_name") or model["slug"]),
        description=str(model.get("description") or ""),
        default_reasoning_effort=(
            str(model["default_reasoning_level"])
            if model.get("default_reasoning_level") is not None
            else None
        ),
        supported_reasoning_efforts=tuple(efforts),
        input_modalities=tuple(str(item) for item in modalities),
    )


def parse_response_sse(sse_text: str) -> CodexResponse | None:
    return parse_response_events(_iter_sse_events(sse_text.splitlines()))


def parse_response_events(
    events: Iterator[dict[str, Any]] | Sequence[dict[str, Any]],
) -> CodexResponse | None:
    output_items: list[dict[str, Any]] = []
    text_deltas: list[str] = []
    completed = False
    response_id: str | None = None
    response_model: str | None = None
    usage: dict[str, Any] | None = None

    for event in events:
        event_type = event.get("type")
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                output_items.append(item)
        elif event_type == "response.output_text.delta" and isinstance(
            event.get("delta"), str
        ):
            text_deltas.append(event["delta"])
        elif event_type in {"response.completed", "response.done"}:
            completed = True
            response = event.get("response")
            if isinstance(response, dict):
                if isinstance(response.get("id"), str):
                    response_id = response["id"]
                if isinstance(response.get("model"), str):
                    response_model = response["model"]
                if isinstance(response.get("usage"), dict):
                    usage = dict(response["usage"])
                if not output_items:
                    fallback = response.get("output")
                    if isinstance(fallback, list):
                        output_items = [
                            item for item in fallback if isinstance(item, dict)
                        ]

    if not completed:
        return None

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output_items:
        item_type = item.get("type")
        if item_type == "message":
            content = item.get("content")
            if isinstance(content, list):
                for chunk in content:
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("type") in {"output_text", "text"} and isinstance(
                        chunk.get("text"), str
                    ):
                        text_parts.append(str(chunk["text"]))
        elif item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            if call_id and name:
                tool_calls.append(
                    ToolCall(call_id, name, _parse_arguments(item.get("arguments")))
                )

    return CodexResponse(
        text=("".join(text_parts) or "".join(text_deltas)).strip(),
        tool_calls=tool_calls,
        output_items=output_items,
        response_id=response_id,
        model=response_model,
        usage=usage,
    )


def _iter_sse_events(lines: Any) -> Iterator[dict[str, Any]]:
    for raw_line in lines:
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)
        line = line.rstrip("\r\n")
        if not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}
