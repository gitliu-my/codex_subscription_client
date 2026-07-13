from __future__ import annotations

"""Direct ChatGPT Codex backend client using subscription OAuth tokens."""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .auth import CodexOAuth, CodexOAuthError, extract_chatgpt_account_id


DEFAULT_BACKEND_URL = "https://chatgpt.com/backend-api/codex/responses"


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

    def generate(self, prompt: str, instructions: str | None = None) -> str:
        response = self.create_response(
            input_items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            instructions=instructions,
        )
        if response.tool_calls:
            names = ", ".join(call.name for call in response.tool_calls)
            raise CodexBackendError(f"模型返回了工具调用而不是最终文本：{names}")
        if not response.text:
            raise CodexBackendError("模型没有返回文本。")
        return response.text

    def create_response(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        instructions: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> CodexResponse:
        payload = self._build_payload(input_items, tools, instructions, extra_body)
        access_token = self.auth.get_access_token(allow_login=self.allow_login)

        try:
            sse_text = self._send(payload, access_token)
        except _UnauthorizedError:
            try:
                access_token = self.auth.refresh().access_token
            except CodexOAuthError:
                if not self.allow_login:
                    raise
                access_token = self.auth.login().access_token
            try:
                sse_text = self._send(payload, access_token)
            except _UnauthorizedError as exc:
                raise CodexBackendError("重新认证后 Codex backend 仍然返回 401。") from exc

        parsed = parse_response_sse(sse_text)
        if parsed is None:
            raise CodexBackendError(
                "Codex backend 没有返回完整 response.completed 事件：\n"
                + sse_text[-2000:]
            )
        return parsed

    def _send(self, payload: dict[str, Any], access_token: str) -> str:
        account_id = extract_chatgpt_account_id(access_token)
        request = urllib.request.Request(
            self.backend_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {access_token}",
                "chatgpt-account-id": account_id,
                "OpenAI-Beta": "responses=experimental",
                "originator": "codex_cli_rs",
                "User-Agent": "codex-subscription-client/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                raise _UnauthorizedError(detail) from exc
            raise CodexBackendError(f"Codex backend 返回 HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CodexBackendError(f"Codex backend 请求失败：{exc}") from exc

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
            "store": False,
            "stream": True,
            "reasoning": {"effort": self.reasoning_effort, "summary": "auto"},
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        if extra_body:
            payload.update(extra_body)
        return payload


class _UnauthorizedError(RuntimeError):
    pass


def parse_response_sse(sse_text: str) -> CodexResponse | None:
    output_items: list[dict[str, Any]] = []
    completed = False

    for line in sse_text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line.removeprefix("data: ").strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                output_items.append(item)
        elif event_type in {"response.completed", "response.done"}:
            completed = True
            response = event.get("response")
            if isinstance(response, dict) and not output_items:
                fallback = response.get("output")
                if isinstance(fallback, list):
                    output_items = [item for item in fallback if isinstance(item, dict)]

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
        text="".join(text_parts).strip(),
        tool_calls=tool_calls,
        output_items=output_items,
    )


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
