from __future__ import annotations

"""Local browser dashboard for authentication and API server management."""

import errno
import json
import http.cookies
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

from .auth import CodexOAuth, CodexOAuthError
from .client import (
    CodexBackendError,
    CodexSubscriptionClient,
    parse_response_events,
)
from .service import probe_api, start_api_service, stop_api_service
from .settings import DEFAULT_SETTINGS_PATH, REASONING_EFFORTS, SettingsStore


class DashboardController:
    def __init__(self, settings_path: Path | None = None) -> None:
        self.settings_path = settings_path or DEFAULT_SETTINGS_PATH
        self.settings = SettingsStore(self.settings_path)
        self.auth = CodexOAuth()
        self.config = self.settings.load_or_create()

    def state(self) -> dict[str, Any]:
        status = self.auth.status()
        api_status = probe_api(self.config)
        return {
            "auth": {
                "logged_in": status.logged_in,
                "expired": status.expired,
            },
            "server": {
                "status": api_status.state,
                "running": api_status.state == "running",
                "port_in_use": api_status.state != "stopped",
                "pid": api_status.pid,
                "url": self.api_url(),
            },
            "config": dict(self.config),
            "reasoning_efforts": list(REASONING_EFFORTS),
        }

    def login(self) -> dict[str, Any]:
        self.auth.login()
        return self.state()

    def logout(self) -> dict[str, Any]:
        if probe_api(self.config).state == "running":
            stop_api_service(self.config)
        self.auth.logout()
        return self.state()

    def models(self) -> list[dict[str, Any]]:
        client = self._client(allow_login=False)
        return [
            {
                "slug": model.slug,
                "display_name": model.display_name,
                "default_reasoning_effort": model.default_reasoning_effort,
                "supported_reasoning_efforts": list(model.supported_reasoning_efforts),
                "input_modalities": list(model.input_modalities),
            }
            for model in client.list_models()
        ]

    def start_api(self, value: dict[str, Any]) -> dict[str, Any]:
        config = self._validated_config(value)
        self.config = config
        self._save_settings()
        start_api_service(config)
        return self.state()

    def stop_api(self) -> dict[str, Any]:
        stop_api_service(self.config)
        return self.state()

    def test(self, value: dict[str, Any]) -> dict[str, Any]:
        mode, api_format, prompt, instructions, model, effort, images, image_tool = (
            self._test_options(value)
        )

        started = time.monotonic()
        if mode == "direct":
            current = {
                **self.config,
                "model": model,
                "reasoning_effort": effort,
            }
            request_body = _responses_test_body(
                prompt,
                instructions,
                images,
                model,
                effort,
                image_tool=image_tool,
            )
            client = self._client(current, allow_login=False)
            if image_tool is None:
                result = client.generate_response(
                    prompt,
                    instructions=instructions or None,
                    images=images,
                )
            else:
                result = client.create_response(
                    input_items=request_body["input"],
                    tools=request_body.get("tools"),
                    instructions=request_body.get("instructions"),
                )
            response_body = {
                "id": result.response_id,
                "model": result.model or model,
                "output_text": result.text,
                "tool_calls": [
                    {
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    for call in result.tool_calls
                ],
                "output": result.output_items,
                "usage": result.usage,
            }
            endpoint = "ChatGPT subscription backend"
            status = 200
        else:
            if probe_api(self.config).state != "running":
                raise ValueError("本地 API 未运行，请先启动服务。")
            if api_format == "chat":
                endpoint = self.api_url("chat")
                request_body = _chat_test_body(
                    prompt, instructions, images, model, effort
                )
            else:
                endpoint = self.api_url("responses")
                request_body = _responses_test_body(
                    prompt,
                    instructions,
                    images,
                    model,
                    effort,
                    image_tool=image_tool,
                )
            status, response_body = self._local_api_request(endpoint, request_body)

        duration_ms = round((time.monotonic() - started) * 1000)
        result_format = api_format if mode == "local_api" else "responses"
        return {
            "mode": mode,
            "api_format": result_format,
            "endpoint": endpoint,
            "status": status,
            "duration_ms": duration_ms,
            "image_count": len(images),
            "text": _response_text(response_body, result_format),
            "usage": response_body.get("usage"),
            "first_token_ms": None,
            "output_tokens_per_second": None,
            "render_items": _response_render_items(response_body, result_format),
            "generated_images": _response_generated_images(response_body),
            "request": _summarize_data_urls(request_body),
            "response": _summarize_generated_image_data(response_body),
        }

    def test_stream(self, value: dict[str, Any]) -> Iterator[dict[str, Any]]:
        mode, api_format, prompt, instructions, model, effort, images, image_tool = (
            self._test_options(value)
        )
        started = time.monotonic()

        if mode == "direct":
            current = {
                **self.config,
                "model": model,
                "reasoning_effort": effort,
            }
            request_body = _responses_test_body(
                prompt,
                instructions,
                images,
                model,
                effort,
                stream=True,
                image_tool=image_tool,
            )
            events = self._client(current, allow_login=False).iter_response_events(
                input_items=request_body["input"],
                tools=request_body.get("tools"),
                instructions=request_body.get("instructions"),
            )
            endpoint = "ChatGPT subscription backend"
            result_format = "responses"
        else:
            if probe_api(self.config).state != "running":
                raise ValueError("本地 API 未运行，请先启动服务。")
            result_format = api_format
            if api_format == "chat":
                endpoint = self.api_url("chat")
                request_body = _chat_test_body(
                    prompt, instructions, images, model, effort, stream=True
                )
            else:
                endpoint = self.api_url("responses")
                request_body = _responses_test_body(
                    prompt,
                    instructions,
                    images,
                    model,
                    effort,
                    stream=True,
                    image_tool=image_tool,
                )
            events = self._local_api_stream(endpoint, request_body)

        request_summary = _summarize_data_urls(request_body)
        yield {
            "type": "start",
            "result": {
                "mode": mode,
                "api_format": result_format,
                "endpoint": endpoint,
                "status": 200,
                "duration_ms": 0,
                "first_token_ms": None,
                "image_count": len(images),
                "text": "",
                "usage": None,
                "output_tokens_per_second": None,
                "render_items": [],
                "generated_images": [],
                "request": request_summary,
                "response": {"object": "stream", "events": []},
            },
        }

        raw_events: list[dict[str, Any]] = []
        text_parts: list[str] = []
        response_model = model
        usage: dict[str, Any] | None = None
        first_token_ms: int | None = None
        generated_images: dict[str, dict[str, Any]] = {}
        try:
            for event in events:
                raw_events.append(event)
                delta = _stream_text_delta(event, result_format)
                event_images = _stream_generated_images(event, result_format)
                for image in event_images:
                    generated_images[image["id"]] = image
                event_usage = _stream_usage(event, result_format)
                event_model = _stream_model(event, result_format)
                if event_model:
                    response_model = event_model
                if event_usage is not None:
                    usage = event_usage
                if delta:
                    text_parts.append(delta)
                    if first_token_ms is None:
                        first_token_ms = round((time.monotonic() - started) * 1000)
                yield {
                    "type": "delta" if delta else "event",
                    "delta": delta,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "usage": event_usage,
                    "images": event_images,
                    "event": _summarize_generated_image_data(event),
                }
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()

        if result_format == "responses":
            parsed = parse_response_events(iter(raw_events))
            if parsed is not None:
                if parsed.text:
                    text_parts = [parsed.text]
                response_model = parsed.model or response_model
                usage = parsed.usage or usage

        duration_ms = round((time.monotonic() - started) * 1000)
        output_rate = _output_tokens_per_second(
            usage, duration_ms, first_token_ms
        )
        response_body = {
            "object": f"{result_format}.stream",
            "model": response_model,
            "usage": usage,
            "events": [
                _summarize_generated_image_data(event) for event in raw_events
            ],
        }
        yield {
            "type": "complete",
            "result": {
                "mode": mode,
                "api_format": result_format,
                "endpoint": endpoint,
                "status": 200,
                "duration_ms": duration_ms,
                "first_token_ms": first_token_ms,
                "image_count": len(images),
                "text": "".join(text_parts),
                "usage": usage,
                "output_tokens_per_second": output_rate,
                "render_items": [],
                "generated_images": list(generated_images.values()),
                "request": request_summary,
                "response": response_body,
            },
        }

    def api_url(self, api_format: str = "chat") -> str:
        path = "responses" if api_format == "responses" else "chat/completions"
        return f"http://{self.config['host']}:{self.config['port']}/v1/{path}"

    def _local_api_request(
        self, endpoint: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config['api_key']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                value = json.loads(response.read().decode("utf-8"))
                status = response.status
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                value = json.loads(detail)
            except json.JSONDecodeError:
                value = {"error": {"message": detail or str(exc)}}
            raise ValueError(
                f"本地 API 返回 HTTP {exc.code}：{_error_message(value)}"
            ) from exc
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise ValueError(f"本地 API 请求失败：{exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("本地 API 返回值不是 JSON 对象。")
        return status, value

    def _local_api_stream(
        self, endpoint: str, body: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config['api_key']}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        try:
            response = urllib.request.urlopen(request, timeout=240)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                value = json.loads(detail)
            except json.JSONDecodeError:
                value = {"error": {"message": detail or str(exc)}}
            raise ValueError(
                f"本地 API 返回 HTTP {exc.code}：{_error_message(value)}"
            ) from exc
        except (OSError, TimeoutError) as exc:
            raise ValueError(f"本地 API 请求失败：{exc}") from exc

        try:
            with response:
                yield from _iter_sse_json(response)
        except (OSError, TimeoutError) as exc:
            raise ValueError(f"本地 API 流式响应中断：{exc}") from exc

    def _test_options(
        self, value: dict[str, Any]
    ) -> tuple[
        str,
        str,
        str,
        str,
        str,
        str,
        list[str],
        dict[str, Any] | None,
    ]:
        mode = str(value.get("mode") or "direct")
        api_format = str(value.get("api_format") or "chat")
        if mode not in {"direct", "local_api"}:
            raise ValueError("测试方式无效。")
        if api_format not in {"chat", "responses"}:
            raise ValueError("API 格式无效。")

        prompt = str(value.get("text") or "").strip() or "只回答：连接成功"
        instructions = str(value.get("instructions") or "").strip()
        model = str(value.get("model") or self.config["model"]).strip()
        effort = str(
            value.get("reasoning_effort") or self.config["reasoning_effort"]
        ).strip()
        if not model or len(model) > 200:
            raise ValueError("模型名称无效。")
        if not effort or len(effort) > 40:
            raise ValueError("推理档位无效。")
        image_tool = _image_generation_tool(value)
        if image_tool is not None and mode == "local_api" and api_format != "responses":
            raise ValueError("图片生成只支持 Responses API。")
        return (
            mode,
            api_format,
            prompt,
            instructions,
            model,
            effort,
            _test_images(value.get("images")),
            image_tool,
        )

    def _client(
        self, config: dict[str, Any] | None = None, allow_login: bool = False
    ) -> CodexSubscriptionClient:
        current = config or self.config
        return CodexSubscriptionClient(
            model=current["model"],
            reasoning_effort=current["reasoning_effort"],
            allow_login=allow_login,
            auth=self.auth,
        )

    def _load_settings(self) -> dict[str, Any]:
        return self.settings.load()

    def _save_settings(self) -> None:
        self.config = self.settings.save(self.config)

    def _validated_config(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.settings.validate(value)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: DashboardController) -> None:
        super().__init__(address, DashboardHandler)
        self.controller = controller
        self.session_token = secrets.token_urlsafe(32)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(
                200,
                DASHBOARD_HTML,
                "text/html; charset=utf-8",
                set_session_cookie=True,
            )
        elif path == "/health":
            self._json(200, {"status": "ok"})
        elif path == "/api/state":
            if not self._session_authorized():
                return
            self._json(200, self.server.controller.state())
        elif path == "/api/models":
            if not self._session_authorized():
                return
            self._call(self.server.controller.models)
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not self._trusted_post():
            return
        try:
            body = self._read_json()
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        routes: dict[str, Callable[[], Any]] = {
            "/api/login": self.server.controller.login,
            "/api/logout": self.server.controller.logout,
            "/api/server/start": lambda: self.server.controller.start_api(body),
            "/api/server/stop": self.server.controller.stop_api,
            "/api/test": lambda: self.server.controller.test(body),
        }
        if path == "/api/test/stream":
            self._stream_call(lambda: self.server.controller.test_stream(body))
            return
        if path == "/api/quit":
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        operation = routes.get(path)
        if operation is None:
            self._json(404, {"error": "Not found"})
            return
        self._call(operation)

    def _call(self, operation: Callable[[], Any]) -> None:
        try:
            self._json(200, operation())
        except (CodexOAuthError, CodexBackendError, OSError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def _stream_call(
        self, operation: Callable[[], Iterator[dict[str, Any]]]
    ) -> None:
        events: Iterator[dict[str, Any]] | None = None
        started = False
        try:
            events = operation()
            first = next(events)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            started = True
            self._write_stream_event(first)
            for event in events:
                self._write_stream_event(event)
        except StopIteration:
            if not started:
                self._json(400, {"error": "流式测试没有返回事件。"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except (CodexOAuthError, CodexBackendError, OSError, ValueError) as exc:
            if not started:
                self._json(400, {"error": str(exc)})
            else:
                try:
                    self._write_stream_event({"type": "error", "error": str(exc)})
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            if events is not None:
                close = getattr(events, "close", None)
                if callable(close):
                    close()

    def _write_stream_event(self, event: dict[str, Any]) -> None:
        encoded = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(encoded)
        self.wfile.flush()

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效。") from exc
        if length == 0:
            return {}
        if length < 0 or length > 20 * 1024 * 1024:
            raise ValueError("请求内容过大。")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("请求必须是 JSON。") from exc
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象。")
        return value

    def _json(self, status: int, value: Any) -> None:
        self._send(
            status,
            json.dumps(value, ensure_ascii=False),
            "application/json; charset=utf-8",
        )

    def _send(
        self,
        status: int,
        body: str,
        content_type: str,
        set_session_cookie: bool = False,
    ) -> None:
        self._send_response(
            status,
            body,
            content_type,
            set_session_cookie=set_session_cookie,
        )

    def _send_response(
        self,
        status: int,
        body: str,
        content_type: str,
        set_session_cookie: bool = False,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        if set_session_cookie:
            self.send_header(
                "Set-Cookie",
                f"codex_dashboard={self.server.session_token}; "
                "HttpOnly; SameSite=Strict; Path=/",
            )
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _session_authorized(self) -> bool:
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        value = cookie.get("codex_dashboard")
        if value and secrets.compare_digest(value.value, self.server.session_token):
            return True
        self._json(401, {"error": "管理页会话无效，请重新打开页面。"})
        return False

    def _trusted_post(self) -> bool:
        if not self._session_authorized():
            return False
        expected_hosts = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        if self.headers.get("Host") not in expected_hosts:
            self._json(403, {"error": "请求来源无效。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in expected_hosts}:
            self._json(403, {"error": "请求来源无效。"})
            return False
        if self.headers.get("X-Codex-Dashboard") != "1":
            self._json(403, {"error": "缺少管理页请求标记。"})
            return False
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            self._json(415, {"error": "请求必须使用 application/json。"})
            return False
        return True

    def log_message(self, format: str, *args: object) -> None:
        return


def _test_images(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("最多可测试 4 张图片。")
    images: list[str] = []
    total_size = 0
    for item in value:
        data_url = item.get("data_url") if isinstance(item, dict) else item
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            raise ValueError("图片必须是浏览器上传的 image data URL。")
        if ";base64," not in data_url[:100]:
            raise ValueError("图片 data URL 必须使用 base64。")
        total_size += len(data_url)
        if len(data_url) > 8 * 1024 * 1024:
            raise ValueError("单张图片不能超过 6 MB。")
        images.append(data_url)
    if total_size > 16 * 1024 * 1024:
        raise ValueError("图片总大小不能超过 12 MB。")
    return images


def _chat_test_body(
    prompt: str,
    instructions: str,
    images: list[str],
    model: str,
    effort: str,
    stream: bool = False,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {"url": image, "detail": "auto"},
        }
        for image in images
    )
    messages.append({"role": "user", "content": content})
    body: dict[str, Any] = {
        "model": model,
        "reasoning_effort": effort,
        "messages": messages,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def _responses_test_body(
    prompt: str,
    instructions: str,
    images: list[str],
    model: str,
    effort: str,
    stream: bool = False,
    image_tool: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    content.extend(
        {
            "type": "input_image",
            "image_url": image,
            "detail": "auto",
        }
        for image in images
    )
    body: dict[str, Any] = {
        "model": model,
        "reasoning": {"effort": effort},
        "input": [{"type": "message", "role": "user", "content": content}],
        "stream": stream,
    }
    if instructions:
        body["instructions"] = instructions
    if image_tool is not None:
        body["tools"] = [image_tool]
    return body


def _image_generation_tool(value: dict[str, Any]) -> dict[str, Any] | None:
    if value.get("image_generation") is not True:
        return None
    quality = str(value.get("image_quality") or "low")
    size = str(value.get("image_size") or "auto")
    if quality not in {"auto", "low", "medium", "high"}:
        raise ValueError("图片质量无效。")
    if size not in {"auto", "1024x1024", "1536x1024", "1024x1536"}:
        raise ValueError("图片尺寸无效。")
    return {
        "type": "image_generation",
        "action": "auto",
        "quality": quality,
        "size": size,
    }


def _iter_sse_json(lines: Any) -> Iterator[dict[str, Any]]:
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _stream_text_delta(event: dict[str, Any], api_format: str) -> str:
    if api_format == "responses":
        delta = event.get("delta")
        if event.get("type") == "response.output_text.delta" and isinstance(
            delta, str
        ):
            return delta
        return ""
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta") if isinstance(choice, dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) else ""


def _stream_usage(
    event: dict[str, Any], api_format: str
) -> dict[str, Any] | None:
    if api_format == "responses":
        response = event.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
    else:
        usage = event.get("usage")
    return dict(usage) if isinstance(usage, dict) else None


def _stream_model(event: dict[str, Any], api_format: str) -> str | None:
    if api_format == "responses":
        response = event.get("response")
        model = response.get("model") if isinstance(response, dict) else None
    else:
        model = event.get("model")
    return model if isinstance(model, str) else None


def _stream_generated_images(
    event: dict[str, Any], api_format: str
) -> list[dict[str, Any]]:
    if api_format != "responses":
        return []
    event_type = event.get("type")
    if event_type == "response.image_generation_call.partial_image":
        data = event.get("partial_image_b64")
        index = event.get("partial_image_index", 0)
        if isinstance(data, str) and data:
            return [
                {
                    "id": str(event.get("item_id") or f"image-{index}"),
                    "data_url": f"data:image/png;base64,{data}",
                    "status": "partial",
                    "index": index if isinstance(index, int) else 0,
                }
            ]

    images: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    item = event.get("item")
    if isinstance(item, dict):
        candidates.append(item)
    response = event.get("response")
    output = response.get("output") if isinstance(response, dict) else None
    if isinstance(output, list):
        candidates.extend(candidate for candidate in output if isinstance(candidate, dict))
    for index, candidate in enumerate(candidates):
        data = candidate.get("result")
        if candidate.get("type") != "image_generation_call" or not isinstance(data, str):
            continue
        images.append(
            {
                "id": str(candidate.get("id") or f"image-{index}"),
                "data_url": f"data:image/png;base64,{data}",
                "status": "completed",
                "index": index,
            }
        )
    return images


def _response_generated_images(value: dict[str, Any]) -> list[dict[str, Any]]:
    return _stream_generated_images(
        {"type": "response.completed", "response": value}, "responses"
    )


def _response_render_items(
    value: dict[str, Any], api_format: str
) -> list[dict[str, Any]]:
    if api_format != "responses":
        text = _response_text(value, api_format)
        return [{"type": "text", "text": text}] if text else []
    items: list[dict[str, Any]] = []
    output = value.get("output")
    if not isinstance(output, list):
        text = _response_text(value, api_format)
        return [{"type": "text", "text": text}] if text else []
    for output_item in output:
        if not isinstance(output_item, dict):
            continue
        if output_item.get("type") == "message":
            content = output_item.get("content")
            if not isinstance(content, list):
                continue
            text = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "output_text"
            )
            if text:
                items.append({"type": "text", "text": text})
        elif output_item.get("type") == "image_generation_call":
            data = output_item.get("result")
            if isinstance(data, str) and data:
                items.append(
                    {
                        "type": "image",
                        "id": str(output_item.get("id") or f"image-{len(items)}"),
                        "data_url": f"data:image/png;base64,{data}",
                        "status": "completed",
                    }
                )
    return items


def _summarize_generated_image_data(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            item_key: _summarize_generated_image_data(item, item_key)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_summarize_generated_image_data(item) for item in value]
    if isinstance(value, str) and key in {"result", "partial_image_b64"}:
        return f"[generated image base64, {len(value):,} chars]"
    return value


def _output_tokens_per_second(
    usage: dict[str, Any] | None,
    duration_ms: int,
    first_token_ms: int | None,
) -> float | None:
    if usage is None or first_token_ms is None:
        return None
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    if not isinstance(output_tokens, int):
        return None
    details = usage.get("output_tokens_details")
    if not isinstance(details, dict):
        details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        details.get("reasoning_tokens", 0) if isinstance(details, dict) else 0
    )
    visible_tokens = max(output_tokens - reasoning_tokens, 0)
    generation_ms = duration_ms - first_token_ms
    if visible_tokens == 0 or generation_ms <= 0:
        return None
    return round(visible_tokens * 1000 / generation_ms, 1)


def _response_text(value: dict[str, Any], api_format: str) -> str:
    if isinstance(value.get("output_text"), str):
        return value["output_text"]
    choices = value.get("choices")
    if api_format == "chat" and isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
    return ""


def _summarize_data_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _summarize_data_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_summarize_data_urls(item) for item in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        media_type = value.split(";", 1)[0].removeprefix("data:")
        return f"[{media_type} base64, {len(value):,} chars]"
    return value


def _error_message(value: Any) -> str:
    if isinstance(value, dict):
        error = value.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return str(value)


def launch_dashboard(
    host: str = "127.0.0.1", port: int = 8320, open_browser: bool = True
) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("管理界面只能监听本机。")
    url = f"http://{host}:{port}"
    try:
        server = DashboardServer((host, port), DashboardController())
    except OSError as exc:
        if _dashboard_is_running(url):
            print(f"Codex Subscription 管理界面已在运行：{url}")
            if open_browser:
                webbrowser.open(url)
            return
        if exc.errno == errno.EADDRINUSE:
            alternative_port = port + 1 if port < 65535 else port - 1
            raise ValueError(
                f"管理界面端口 {port} 已被其他程序占用；"
                f"请运行 csub ui --port {alternative_port}。"
            ) from None
        raise ValueError(f"管理界面启动失败：{exc}") from exc
    print(f"Codex Subscription 管理界面：{url}")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _dashboard_is_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    else:
        if isinstance(value, dict) and value.get("status") == "ok":
            return True

    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            page = response.read(128 * 1024).decode("utf-8", errors="ignore")
    except OSError:
        return False
    return (
        "<title>Codex Subscription</title>" in page
        and "<h1>Codex Subscription</h1>" in page
    )


DASHBOARD_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Codex Subscription</title>
<style>
:root{color-scheme:light;--bg:#edf1f3;--surface:#fff;--surface-2:#f6f8f9;--ink:#172126;--muted:#68777f;--line:#d8e0e4;--line-strong:#b9c5cb;--blue:#1768c4;--blue-soft:#eaf3fd;--teal:#13795b;--teal-soft:#e8f6f0;--red:#b63b34;--red-soft:#fff0ef;--focus:#9bc8f5;--top:#11191d}
*{box-sizing:border-box}html,body{min-height:100%}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:0}
button,input,select,textarea{font:inherit;letter-spacing:0}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.48}
.topbar{height:72px;background:var(--top);color:#fff;border-bottom:3px solid #2c9a71}.topbar-inner{height:100%;max-width:1680px;margin:auto;padding:0 28px;display:flex;align-items:center;justify-content:space-between;gap:24px}
.brand h1{font-size:19px;line-height:1.2;margin:0;font-weight:700}.brand p{font-size:12px;color:#9fb0b8;margin:4px 0 0}.global-status{display:flex;align-items:center;gap:10px;font-weight:650;font-size:13px}.dot{width:9px;height:9px;border-radius:50%;background:#77858c;box-shadow:0 0 0 4px rgba(119,133,140,.12)}.dot.on{background:#3fc28a;box-shadow:0 0 0 4px rgba(63,194,138,.14)}
.page{max-width:1680px;margin:auto;padding:20px 28px 32px}.notice{min-height:40px;display:flex;align-items:center;padding:9px 14px;margin-bottom:16px;border:1px solid #c9dff5;border-left:3px solid var(--blue);border-radius:4px;background:var(--blue-soft);color:#174f86}.notice.error{border-color:#efc5c2;border-left-color:var(--red);background:var(--red-soft);color:#842b26}.notice.success{border-color:#bde0d1;border-left-color:var(--teal);background:var(--teal-soft);color:#176548}
.workspace{display:grid;grid-template-columns:minmax(340px,420px) minmax(0,1fr);gap:18px;align-items:stretch;min-height:calc(100vh - 180px)}.panel{background:var(--surface);border:1px solid var(--line);border-radius:7px;box-shadow:0 1px 2px rgba(18,33,40,.04)}
.control-panel{display:flex;flex-direction:column}.control-section{padding:20px 22px;border-bottom:1px solid var(--line)}.control-section:last-child{border-bottom:0}.section-title{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:15px}.section-title h2,.lab-title h2{font-size:15px;line-height:1.3;margin:0;font-weight:700}.section-title p,.lab-title p{font-size:12px;color:var(--muted);margin:4px 0 0}.state-line{font-size:14px;font-weight:650}.meta{color:var(--muted);font-size:12px;margin-top:4px;overflow-wrap:anywhere}.actions{display:flex;gap:8px;flex-wrap:wrap}
.button{min-height:36px;border:1px solid var(--line-strong);border-radius:4px;background:#fff;color:#27363d;padding:7px 12px;font-weight:650}.button:hover{background:#f2f5f6}.button.primary{background:var(--blue);border-color:var(--blue);color:#fff}.button.primary:hover{background:#115bad}.button.danger{border-color:#dca8a4;color:var(--red)}.button.compact{min-height:34px;padding:6px 10px;font-size:12px}
label.field-label{display:block;color:#45545b;font-size:12px;font-weight:650;margin:12px 0 5px}.field,.select,.textarea{width:100%;border:1px solid var(--line-strong);border-radius:4px;background:#fff;color:var(--ink);padding:8px 10px}.field,.select{height:38px}.textarea{resize:vertical;min-height:76px}.field:focus,.select:focus,.textarea:focus{outline:3px solid var(--focus);outline-offset:0;border-color:var(--blue)}.field.mono{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}.two-fields{display:grid;grid-template-columns:minmax(0,1fr) 124px;gap:0 12px}.input-action{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px}.config-actions{margin-top:14px}
.lab{display:flex;min-width:0;flex-direction:column}.lab-head{padding:20px 24px 16px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.lab-title h2{font-size:17px}.mode-stack{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}.segmented{display:inline-flex;border:1px solid var(--line-strong);border-radius:5px;overflow:hidden;background:var(--surface-2);height:36px}.segmented label{margin:0}.segmented input{position:absolute;opacity:0;pointer-events:none}.segmented span{height:34px;display:flex;align-items:center;padding:0 12px;border-right:1px solid var(--line-strong);font-size:12px;font-weight:650;color:#536269;cursor:pointer;white-space:nowrap}.segmented label:last-child span{border-right:0}.segmented input:checked+span{background:#203038;color:#fff}.segmented input:focus-visible+span{outline:3px solid var(--focus);outline-offset:-3px}.segmented.dim{opacity:.5;pointer-events:none}
.composer{padding:18px 24px 16px;display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,34%);gap:18px;border-bottom:1px solid var(--line)}.prompt-column,.media-column{min-width:0}.prompt-column .textarea.prompt{min-height:148px}.media-column{display:flex;flex-direction:column}.upload-zone{min-height:118px;border:1px dashed #99aab2;border-radius:5px;background:var(--surface-2);display:flex;align-items:center;justify-content:center;text-align:center;padding:16px;color:var(--muted);transition:border-color .15s,background .15s}.upload-zone.drag{border-color:var(--blue);background:var(--blue-soft)}.upload-zone strong{display:block;color:#34434a;margin-bottom:5px}.upload-zone .button{margin-top:10px;display:inline-flex;align-items:center}.image-input{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden}.image-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}.image-item{position:relative;min-width:0;border:1px solid var(--line);border-radius:4px;overflow:hidden;background:#fff}.image-item img{display:block;width:100%;height:82px;object-fit:cover;background:#e7ecee}.image-info{padding:6px 7px;font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.image-remove{position:absolute;right:5px;top:5px;width:26px;height:26px;border:1px solid rgba(255,255,255,.8);border-radius:4px;background:rgba(17,25,29,.8);color:#fff;font-size:15px;line-height:1}.image-output-control{margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}.image-output-options{display:grid;grid-template-columns:1fr 1fr;gap:10px}.image-output-options .field-label{margin-top:10px}
.runbar{padding:14px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:16px;background:#fbfcfc}.run-context{font-size:12px;color:var(--muted)}.run-context strong{color:#37464d}.run-actions{display:flex;align-items:center;gap:16px}.stream-toggle{display:inline-flex;align-items:center;gap:8px;color:#425159;font-size:12px;font-weight:650;cursor:pointer;white-space:nowrap}.stream-toggle input{position:absolute;opacity:0;pointer-events:none}.toggle-track{position:relative;width:34px;height:20px;border:1px solid #97a7af;border-radius:10px;background:#e6ebed;transition:background .15s,border-color .15s}.toggle-track::after{content:"";position:absolute;width:14px;height:14px;left:2px;top:2px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(23,33,38,.28);transition:transform .15s}.stream-toggle input:checked+.toggle-track{border-color:var(--teal);background:var(--teal)}.stream-toggle input:checked+.toggle-track::after{transform:translateX(14px)}.stream-toggle input:focus-visible+.toggle-track{outline:3px solid var(--focus)}.stream-toggle input:disabled~*{opacity:.5;cursor:not-allowed}.run-button{min-width:150px}
.result-area{display:flex;min-height:300px;flex:1;flex-direction:column}.result-head{padding:14px 24px 0;display:flex;align-items:center;justify-content:space-between;gap:16px}.metrics{display:flex;gap:18px;flex-wrap:wrap;color:var(--muted);font-size:12px}.metrics strong{display:block;color:var(--ink);font-size:13px}.tabs{display:flex;gap:18px;border-bottom:1px solid var(--line);padding:0 24px;margin-top:10px}.tab{border:0;border-bottom:2px solid transparent;background:transparent;color:var(--muted);padding:10px 1px 9px;font-weight:650}.tab.active{border-bottom-color:var(--blue);color:var(--blue)}.result-view{margin:0;padding:18px 24px 24px;min-height:220px;white-space:pre-wrap;overflow:auto;overflow-wrap:anywhere;color:#25343b;font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace}.result-view.output{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;white-space:normal}.output-text{white-space:pre-wrap;margin:0 0 14px}.generated-figure{margin:0 0 18px}.generated-figure img{display:block;max-width:100%;max-height:720px;object-fit:contain;border:1px solid var(--line);border-radius:4px;background:#eef2f3}.generated-caption{display:flex;align-items:center;gap:12px;margin-top:8px;color:var(--muted);font-size:12px}.generated-caption a{color:var(--blue);font-weight:650;text-decoration:none}.empty{color:#7b898f}
.hidden{display:none!important}
@media(min-width:1500px){.workspace{grid-template-columns:420px minmax(0,1fr)}.composer{grid-template-columns:minmax(0,1fr) 360px}.prompt-column .textarea.prompt{min-height:180px}.result-view{min-height:300px}}
@media(max-width:980px){.workspace{grid-template-columns:1fr}.composer{grid-template-columns:1fr}.media-column{max-width:none}.lab-head{flex-direction:column}.mode-stack{justify-content:flex-start}.image-list{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:640px){.topbar{height:auto}.topbar-inner{padding:14px 18px;align-items:flex-start;flex-direction:column}.page{padding:14px 12px 24px}.workspace{min-height:0}.control-section,.lab-head,.composer,.runbar{padding-left:16px;padding-right:16px}.two-fields{grid-template-columns:1fr}.mode-stack{width:100%;flex-direction:column}.segmented{display:flex;width:100%}.segmented label{flex:1}.segmented span{justify-content:center}.input-action{grid-template-columns:1fr}.runbar{align-items:stretch;flex-direction:column}.run-actions{align-items:stretch;flex-direction:column}.stream-toggle{min-height:36px}.run-button{width:100%}.result-head{align-items:flex-start;flex-direction:column;padding-left:16px;padding-right:16px}.tabs,.result-view{padding-left:16px;padding-right:16px}.image-list{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner"><div class="brand"><h1>Codex Subscription</h1><p>订阅模型与本地 OpenAI-compatible API 控制台</p></div><div class="global-status"><span id="serverDot" class="dot"></span><span id="serverLabel">API 已停止</span></div></div></header>
<main class="page">
<div id="notice" class="notice">正在读取本地状态...</div>
<div class="workspace">
<aside class="panel control-panel">
  <section class="control-section"><div class="section-title"><div><h2>ChatGPT 登录</h2><p>订阅凭据仅保存在本机</p></div></div><div class="state-line" id="authText">未登录</div><div id="account" class="meta"></div><div class="actions config-actions"><button id="loginBtn" class="button primary" onclick="login()">网页登录</button><button id="logoutBtn" class="button" onclick="logout()">退出登录</button></div></section>
  <section class="control-section"><div class="section-title"><div><h2>API 服务</h2><p id="serverMeta">仅监听本机，不向局域网开放</p></div><div id="apiState" class="state-line">已停止</div></div><div class="actions"><button id="startBtn" class="button primary" onclick="startServer()">启动服务</button><button id="stopBtn" class="button danger" onclick="stopServer()">停止</button></div></section>
  <section class="control-section"><div class="section-title"><div><h2>运行配置</h2><p>CLI、后台服务与测试工作台共用</p></div></div><div class="two-fields"><div><label class="field-label" for="model">模型</label><select class="select" id="model"><option value="gpt-5.6-luna">gpt-5.6-luna</option></select></div><div><label class="field-label" for="effort">推理档位</label><select class="select" id="effort"></select></div></div><label class="field-label" for="apiKey">本地 API Key</label><div class="input-action"><input class="field mono" id="apiKey" type="password" autocomplete="off"><button class="button compact" onclick="toggleKey(event)">显示</button></div><div class="two-fields"><div><label class="field-label" for="port">端口</label><input class="field" id="port" type="number" min="1" max="65535"></div><div><label class="field-label" for="maxConcurrency">最大并发</label><input class="field" id="maxConcurrency" type="number" min="1" max="32"></div></div><label class="field-label" for="endpoint">当前 API 地址</label><div class="input-action"><input class="field mono" id="endpoint" readonly><button class="button compact" onclick="copyEndpoint()">复制</button></div><div class="actions config-actions"><button class="button" onclick="loadModels()">刷新模型</button><button class="button" onclick="copyKey()">复制 Key</button></div></section>
</aside>
<section class="panel lab">
  <div class="lab-head"><div class="lab-title"><h2>调用实验台</h2><p>验证订阅后端，或像第三方客户端一样请求本地 API</p></div><div class="mode-stack"><div id="testMode" class="segmented"><label><input type="radio" name="testMode" value="direct" checked><span>订阅直连</span></label><label><input type="radio" name="testMode" value="local_api"><span>本地 API</span></label></div><div id="apiFormat" class="segmented dim"><label><input type="radio" name="apiFormat" value="chat" checked><span>Chat Completions</span></label><label><input type="radio" name="apiFormat" value="responses"><span>Responses</span></label></div></div></div>
  <div class="composer"><div class="prompt-column"><label class="field-label" for="instructions">系统提示词</label><textarea class="textarea" id="instructions" placeholder="可选，例如：使用中文简洁回答。">使用中文简洁回答，不要解释系统配置。</textarea><label class="field-label" for="testText">用户输入</label><textarea class="textarea prompt" id="testText">请说明你接收到了哪些输入，并只回答关键信息。</textarea></div><div class="media-column"><label class="field-label">图片输入</label><div id="uploadZone" class="upload-zone"><div><strong>拖入图片或从本机选择</strong><div>最多 4 张，单张不超过 6 MB</div><label class="button compact" for="imageInput">选择图片</label><input id="imageInput" class="image-input" type="file" accept="image/*" multiple></div></div><div id="imageList" class="image-list"></div><div class="image-output-control"><label class="stream-toggle"><input id="imageGeneration" type="checkbox"><span class="toggle-track" aria-hidden="true"></span><span>图片生成</span></label><div id="imageGenerationOptions" class="image-output-options hidden"><div><label class="field-label" for="imageQuality">质量</label><select class="select" id="imageQuality"><option value="low" selected>low</option><option value="medium">medium</option><option value="high">high</option><option value="auto">auto</option></select></div><div><label class="field-label" for="imageSize">尺寸</label><select class="select" id="imageSize"><option value="auto" selected>auto</option><option value="1024x1024">1024x1024</option><option value="1536x1024">1536x1024</option><option value="1024x1536">1024x1536</option></select></div></div></div></div></div>
  <div class="runbar"><div id="runContext" class="run-context"><strong>订阅直连</strong><br>绕过本地 API，直接验证 OAuth 与模型后端</div><div class="run-actions"><label class="stream-toggle"><input id="streamMode" type="checkbox" checked><span class="toggle-track" aria-hidden="true"></span><span>流式输出</span></label><button id="runBtn" class="button primary run-button" onclick="runTest()">运行直连测试</button></div></div>
  <div class="result-area"><div class="result-head"><div class="metrics"><div><span>状态</span><strong id="metricStatus">等待</strong></div><div><span>首字</span><strong id="metricFirstToken">-</strong></div><div><span>总耗时</span><strong id="metricDuration">-</strong></div><div><span>输出速率</span><strong id="metricRate">-</strong></div><div><span>Token（入 / 出 / 总）</span><strong id="metricUsage">-</strong></div><div><span>模型</span><strong id="metricModel">-</strong></div><div><span>输入图片</span><strong id="metricImages">0</strong></div><div><span>生成图片</span><strong id="metricGeneratedImages">0</strong></div></div></div><div class="tabs"><button class="tab active" data-view="output" onclick="showResult('output')">输出</button><button class="tab" data-view="response" onclick="showResult('response')">原始响应</button><button class="tab" data-view="request" onclick="showResult('request')">实际请求</button></div><div id="testResult" class="result-view output empty">运行测试后，结果会显示在这里。</div></div>
</section>
</div>
</main>
<script>
const $=id=>document.getElementById(id);let state=null,subscriptionModels=[],loadingModels=false,modelsAttempted=false,formInitialized=false,images=[],lastTest=null,resultView='output',testRunning=false,rateSamples=[];
async function request(path,options={}){const response=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json','X-Codex-Dashboard':'1'},...options});const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data}
async function streamRequest(path,payload,onEvent){const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Codex-Dashboard':'1'},body:JSON.stringify(payload)});if(!response.ok){let data={};try{data=await response.json()}catch{}throw new Error(data.error||`请求失败：HTTP ${response.status}`)}if(!response.body)throw new Error('当前浏览器无法读取流式响应。');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';const consume=()=>{const lines=buffer.split('\n');buffer=lines.pop()||'';for(const line of lines){if(line.trim())onEvent(JSON.parse(line))}};while(true){const{value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});consume();if(done)break}if(buffer.trim())onEvent(JSON.parse(buffer))}
function notice(text,type='info'){$('notice').textContent=text;$('notice').className=`notice ${type==='error'?'error':type==='success'?'success':''}`.trim()}
function config(){return{host:'127.0.0.1',port:Number($('port').value),api_key:$('apiKey').value,model:$('model').value,reasoning_effort:$('effort').value,max_concurrency:Number($('maxConcurrency').value)}}
function selected(name){return document.querySelector(`input[name="${name}"]:checked`)?.value}
function setOptions(select,values,preferred){select.replaceChildren(...values.map(value=>{const option=document.createElement('option');option.value=value;option.textContent=value;return option}));select.value=values.includes(preferred)?preferred:(values[0]||'')}
function render(value){state=value;const auth=value.auth,server=value.server,c=value.config;const labels={running:'API 运行中',stopped:'API 已停止',key_mismatch:'API Key 不匹配',port_in_use:'端口被占用'};$('authText').textContent=auth.logged_in?(auth.expired?'登录已过期':'已登录'):'未登录';$('account').textContent=auth.logged_in?'OAuth 登录态可用':'';$('loginBtn').disabled=auth.logged_in&&!auth.expired;$('logoutBtn').disabled=!auth.logged_in;$('serverDot').className=server.running?'dot on':'dot';$('serverLabel').textContent=labels[server.status]||'API 状态未知';$('apiState').textContent=server.running?'运行中':labels[server.status]||'未知';$('serverMeta').textContent=server.running?`后台进程${server.pid?` PID ${server.pid}`:''}，关闭页面不会停止`:'仅监听本机，不向局域网开放';$('startBtn').disabled=server.status!=='stopped'||!auth.logged_in;$('stopBtn').disabled=server.status!=='running';if(!formInitialized){$('model').value=c.model;$('apiKey').value=c.api_key;$('port').value=c.port;$('maxConcurrency').value=c.max_concurrency||3;setEffortOptions(c.reasoning_effort);formInitialized=true}syncEndpoint();syncTestMode()}
function setEffortOptions(preferred){const model=subscriptionModels.find(item=>item.slug===$('model').value);const efforts=model?.supported_reasoning_efforts?.length?model.supported_reasoning_efforts:(state?.reasoning_efforts||['low','medium','high']);const fallback=model?.default_reasoning_effort||efforts[0];setOptions($('effort'),efforts,efforts.includes(preferred)?preferred:fallback)}
function renderModels(preferred){const current=subscriptionModels.some(item=>item.slug===preferred)?preferred:subscriptionModels[0]?.slug;if(!current)return;setOptions($('model'),subscriptionModels.map(item=>item.slug),current);setEffortOptions($('effort').value)}
function syncEndpoint(){if(!state)return;const format=selected('apiFormat');const path=format==='responses'?'responses':'chat/completions';$('endpoint').value=`http://127.0.0.1:${$('port').value||state.config.port}/v1/${path}`}
function syncTestMode(){const mode=selected('testMode'),local=mode==='local_api',imageMode=$('imageGeneration').checked;if(local&&imageMode)document.querySelector('input[name="apiFormat"][value="responses"]').checked=true;$('apiFormat').classList.toggle('dim',!local);$('apiFormat').querySelectorAll('input').forEach(input=>input.disabled=!local||testRunning||(imageMode&&input.value==='chat'));$('imageGenerationOptions').classList.toggle('hidden',!imageMode);const format=selected('apiFormat')==='responses'?'Responses':'Chat Completions';$('runContext').innerHTML=local?`<strong>本地 API · ${format}</strong><br>通过 Bearer Key 请求真实的 127.0.0.1 服务`:'<strong>订阅直连</strong><br>绕过本地 API，直接验证 OAuth 与模型后端';$('runBtn').textContent=testRunning?'请求中...':local?'调用本地 API':'运行直连测试';$('runBtn').disabled=testRunning||!state?.auth.logged_in||(local&&!state?.server.running);$('streamMode').disabled=testRunning;$('imageGeneration').disabled=testRunning;$('imageQuality').disabled=testRunning;$('imageSize').disabled=testRunning;syncEndpoint()}
async function refresh(silent=false){try{const value=await request('/api/state');render(value);if(value.auth.logged_in&&!modelsAttempted)await loadModels(true);if(!silent)notice('本地状态已就绪。','success')}catch(e){if(!silent)notice(e.message,'error')}}
async function action(label,path,body={}){notice(`${label}...`);try{const data=await request(path,{method:'POST',body:JSON.stringify(body)});if(data.auth)render(data);notice(`${label}完成。`,'success');return data}catch(e){notice(e.message,'error');throw e}}
async function login(){await action('等待浏览器授权','/api/login');modelsAttempted=false;await loadModels()}
async function logout(){await action('退出登录','/api/logout');subscriptionModels=[];modelsAttempted=false}
async function startServer(){await action('启动 API','/api/server/start',config())}
async function stopServer(){await action('停止 API','/api/server/stop')}
async function loadModels(silent=false){if(loadingModels)return;loadingModels=true;modelsAttempted=true;if(!silent)notice('正在查询订阅模型...');try{const preferred=$('model').value||state.config.model;subscriptionModels=await request('/api/models');renderModels(preferred);if(!silent)notice(`已加载 ${subscriptionModels.length} 个订阅模型。`,'success')}catch(e){if(!silent)notice(e.message,'error')}finally{loadingModels=false}}
function readFile(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error(`无法读取 ${file.name}`));reader.readAsDataURL(file)})}
async function addImages(files){const candidates=[...files].filter(file=>file.type.startsWith('image/'));for(const file of candidates){if(images.length>=4){notice('最多只能添加 4 张图片。','error');break}if(file.size>6*1024*1024){notice(`${file.name} 超过 6 MB。`,'error');continue}const data_url=await readFile(file);images.push({name:file.name,type:file.type,size:file.size,data_url})}renderImages();$('imageInput').value=''}
function renderImages(){$('imageList').replaceChildren(...images.map((item,index)=>{const card=document.createElement('div');card.className='image-item';const img=document.createElement('img');img.src=item.data_url;img.alt=item.name;const info=document.createElement('div');info.className='image-info';info.textContent=`${item.name} · ${(item.size/1024).toFixed(0)} KB`;const remove=document.createElement('button');remove.className='image-remove';remove.type='button';remove.textContent='×';remove.title='移除图片';remove.onclick=()=>{images.splice(index,1);renderImages()};card.append(img,info,remove);return card}))}
function usageText(usage){if(!usage)return'-';const input=usage.input_tokens??usage.prompt_tokens,output=usage.output_tokens??usage.completion_tokens,total=usage.total_tokens;return`${input??'-'} / ${output??'-'} / ${total??'-'}`}
function estimateTokens(text){let cjk=0,other='';for(const char of text){if(/[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]/.test(char))cjk++;else other+=char}return cjk+Math.ceil(other.replace(/\s+/g,' ').trim().length/4)}
function liveRate(delta,elapsed){if(!delta)return null;rateSamples.push({elapsed,text:delta});rateSamples=rateSamples.filter(sample=>sample.elapsed>=elapsed-2000);const first=rateSamples[0]?.elapsed??elapsed,span=elapsed-first;if(span<350)return null;const tokens=estimateTokens(rateSamples.map(sample=>sample.text).join(''));return tokens>0?tokens*1000/span:null}
function updateTestMetrics(result){$('metricStatus').textContent=result?.streaming?'流式接收':result?.status?`HTTP ${result.status}`:'请求中';$('metricFirstToken').textContent=result?.first_token_ms!=null?`${result.first_token_ms} ms`:'-';$('metricDuration').textContent=result?.duration_ms?`${result.duration_ms} ms`:'-';$('metricRate').textContent=result?.output_tokens_per_second!=null?`${Number(result.output_tokens_per_second).toFixed(1)} tok/s`:result?.live_rate!=null?`约 ${Number(result.live_rate).toFixed(1)} tok/s`:'-';$('metricUsage').textContent=usageText(result?.usage||result?.response?.usage);$('metricModel').textContent=result?.response?.model||$('model').value;$('metricImages').textContent=String(result?.image_count||0);$('metricGeneratedImages').textContent=String(result?.generated_images?.length||0)}
function appendTextItem(result,text){if(!text)return;const items=result.render_items||(result.render_items=[]),last=items[items.length-1];if(last?.type==='text')last.text+=text;else items.push({type:'text',text})}
function upsertImageItem(result,image){const images=result.generated_images||(result.generated_images=[]),existing=images.find(item=>item.id===image.id);if(existing)Object.assign(existing,image);else images.push({...image});const items=result.render_items||(result.render_items=[]),rendered=items.find(item=>item.type==='image'&&item.id===image.id);if(rendered)Object.assign(rendered,image);else items.push({type:'image',...image})}
function consumeStreamEvent(message){if(message.type==='error')throw new Error(message.error||'流式请求失败。');if(message.type==='start'){lastTest={...message.result,streaming:true};rateSamples=[];updateTestMetrics(lastTest);showResult('output');return}if(!lastTest)return;if(message.event)lastTest.response.events.push(message.event);if(message.delta){lastTest.text+=message.delta;appendTextItem(lastTest,message.delta);if(lastTest.first_token_ms==null)lastTest.first_token_ms=message.elapsed_ms;const rate=liveRate(message.delta,message.elapsed_ms);if(rate!=null)lastTest.live_rate=rate}for(const image of message.images||[])upsertImageItem(lastTest,image);if(message.usage){lastTest.usage=message.usage;lastTest.response.usage=message.usage}updateTestMetrics(lastTest);if(resultView==='output')showResult('output');if(message.type==='complete'){const renderItems=lastTest.render_items,generatedImages=lastTest.generated_images;lastTest={...message.result,render_items:renderItems?.length?renderItems:message.result.render_items,generated_images:generatedImages?.length?generatedImages:message.result.generated_images};updateTestMetrics(lastTest);showResult(resultView)}}
async function runTest(){const mode=selected('testMode');if(mode==='local_api'&&!state?.server.running){notice('请先启动本地 API 服务。','error');return}testRunning=true;syncTestMode();lastTest=null;rateSamples=[];$('testResult').textContent='正在建立连接...';$('testResult').className='result-view output';$('metricStatus').textContent='连接中';$('metricFirstToken').textContent='-';$('metricDuration').textContent='-';$('metricRate').textContent='-';$('metricUsage').textContent='-';$('metricGeneratedImages').textContent='0';const payload={mode,api_format:selected('apiFormat'),text:$('testText').value,instructions:$('instructions').value,model:$('model').value,reasoning_effort:$('effort').value,images,image_generation:$('imageGeneration').checked,image_quality:$('imageQuality').value,image_size:$('imageSize').value};try{if($('streamMode').checked)await streamRequest('/api/test/stream',payload,consumeStreamEvent);else{lastTest=await request('/api/test',{method:'POST',body:JSON.stringify(payload)});updateTestMetrics(lastTest);showResult('output')}notice(mode==='local_api'?'本地 API 链路测试成功。':'订阅直连测试成功。','success')}catch(e){lastTest={error:e.message};$('metricStatus').textContent='失败';$('metricDuration').textContent='-';$('metricRate').textContent='-';notice(e.message,'error');showResult('output')}finally{testRunning=false;syncTestMode()}}
function renderOutput(box,result){box.replaceChildren();let items=result.render_items||[];if(!items.length){if(result.text)items=[{type:'text',text:result.text}];else if(result.streaming){box.textContent='等待模型返回...';return}else{box.textContent='模型没有返回可展示内容，可在“原始响应”中查看结构化输出。';return}}for(const [index,item] of items.entries()){if(item.type==='text'){const text=document.createElement('div');text.className='output-text';text.textContent=item.text;box.append(text)}else if(item.type==='image'&&item.data_url){const figure=document.createElement('figure');figure.className='generated-figure';const image=document.createElement('img');image.src=item.data_url;image.alt=`模型生成图片 ${index+1}`;const caption=document.createElement('figcaption');caption.className='generated-caption';const status=document.createElement('span');status.textContent=item.status==='partial'?'生成中':'生成完成';const download=document.createElement('a');download.href=item.data_url;download.download=`csub-generated-${index+1}.png`;download.textContent='下载图片';caption.append(status,download);figure.append(image,caption);box.append(figure)}}}
function showResult(view){resultView=view;document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.view===view));const box=$('testResult');box.className=`result-view ${view==='output'?'output':''}`;if(!lastTest){box.textContent='运行测试后，结果会显示在这里。';box.classList.add('empty');return}if(lastTest.error){box.textContent=lastTest.error;return}if(view==='output')renderOutput(box,lastTest);else if(view==='response')box.textContent=JSON.stringify(lastTest.response,null,2);else box.textContent=JSON.stringify({mode:lastTest.mode,endpoint:lastTest.endpoint,body:lastTest.request},null,2)}
async function copyValue(value,label){await navigator.clipboard.writeText(value);notice(`${label}已复制。`,'success')}
function copyEndpoint(){copyValue($('endpoint').value,'接口地址')}
function copyKey(){copyValue($('apiKey').value,'API Key')}
function toggleKey(event){const input=$('apiKey');input.type=input.type==='password'?'text':'password';event.currentTarget.textContent=input.type==='password'?'显示':'隐藏'}
$('model').addEventListener('change',()=>setEffortOptions($('effort').value));$('port').addEventListener('input',syncEndpoint);document.querySelectorAll('input[name="testMode"],input[name="apiFormat"],#imageGeneration').forEach(input=>input.addEventListener('change',syncTestMode));$('imageInput').addEventListener('change',event=>addImages(event.target.files));const zone=$('uploadZone');['dragenter','dragover'].forEach(name=>zone.addEventListener(name,event=>{event.preventDefault();zone.classList.add('drag')}));['dragleave','drop'].forEach(name=>zone.addEventListener(name,event=>{event.preventDefault();zone.classList.remove('drag')}));zone.addEventListener('drop',event=>addImages(event.dataTransfer.files));refresh();setInterval(()=>refresh(true),5000);window.addEventListener('focus',()=>refresh(true));document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true)});
</script>
</body></html>'''
