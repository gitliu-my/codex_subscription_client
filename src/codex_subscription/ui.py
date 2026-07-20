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
from typing import Any, Callable

from .auth import CodexOAuth, CodexOAuthError
from .client import CodexBackendError, CodexSubscriptionClient
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
        images = _test_images(value.get("images"))

        started = time.monotonic()
        if mode == "direct":
            current = {
                **self.config,
                "model": model,
                "reasoning_effort": effort,
            }
            result = self._client(current, allow_login=False).generate_response(
                prompt,
                instructions=instructions or None,
                images=images,
            )
            request_body = _responses_test_body(
                prompt, instructions, images, model, effort
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
                    prompt, instructions, images, model, effort
                )
            status, response_body = self._local_api_request(endpoint, request_body)

        duration_ms = round((time.monotonic() - started) * 1000)
        return {
            "mode": mode,
            "api_format": api_format if mode == "local_api" else "responses",
            "endpoint": endpoint,
            "status": status,
            "duration_ms": duration_ms,
            "image_count": len(images),
            "text": _response_text(response_body, api_format),
            "request": _summarize_data_urls(request_body),
            "response": response_body,
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
    return {
        "model": model,
        "reasoning_effort": effort,
        "messages": messages,
        "stream": False,
    }


def _responses_test_body(
    prompt: str,
    instructions: str,
    images: list[str],
    model: str,
    effort: str,
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
        "stream": False,
    }
    if instructions:
        body["instructions"] = instructions
    return body


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
.composer{padding:18px 24px 16px;display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,34%);gap:18px;border-bottom:1px solid var(--line)}.prompt-column,.media-column{min-width:0}.prompt-column .textarea.prompt{min-height:148px}.media-column{display:flex;flex-direction:column}.upload-zone{min-height:118px;border:1px dashed #99aab2;border-radius:5px;background:var(--surface-2);display:flex;align-items:center;justify-content:center;text-align:center;padding:16px;color:var(--muted);transition:border-color .15s,background .15s}.upload-zone.drag{border-color:var(--blue);background:var(--blue-soft)}.upload-zone strong{display:block;color:#34434a;margin-bottom:5px}.upload-zone .button{margin-top:10px;display:inline-flex;align-items:center}.image-input{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden}.image-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}.image-item{position:relative;min-width:0;border:1px solid var(--line);border-radius:4px;overflow:hidden;background:#fff}.image-item img{display:block;width:100%;height:82px;object-fit:cover;background:#e7ecee}.image-info{padding:6px 7px;font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.image-remove{position:absolute;right:5px;top:5px;width:26px;height:26px;border:1px solid rgba(255,255,255,.8);border-radius:4px;background:rgba(17,25,29,.8);color:#fff;font-size:15px;line-height:1}
.runbar{padding:14px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:16px;background:#fbfcfc}.run-context{font-size:12px;color:var(--muted)}.run-context strong{color:#37464d}.run-button{min-width:150px}
.result-area{display:flex;min-height:300px;flex:1;flex-direction:column}.result-head{padding:14px 24px 0;display:flex;align-items:center;justify-content:space-between;gap:16px}.metrics{display:flex;gap:18px;flex-wrap:wrap;color:var(--muted);font-size:12px}.metrics strong{display:block;color:var(--ink);font-size:13px}.tabs{display:flex;gap:18px;border-bottom:1px solid var(--line);padding:0 24px;margin-top:10px}.tab{border:0;border-bottom:2px solid transparent;background:transparent;color:var(--muted);padding:10px 1px 9px;font-weight:650}.tab.active{border-bottom-color:var(--blue);color:var(--blue)}.result-view{margin:0;padding:18px 24px 24px;min-height:220px;white-space:pre-wrap;overflow:auto;overflow-wrap:anywhere;color:#25343b;font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace}.result-view.output{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px}.empty{color:#7b898f}
.hidden{display:none!important}
@media(min-width:1500px){.workspace{grid-template-columns:420px minmax(0,1fr)}.composer{grid-template-columns:minmax(0,1fr) 360px}.prompt-column .textarea.prompt{min-height:180px}.result-view{min-height:300px}}
@media(max-width:980px){.workspace{grid-template-columns:1fr}.composer{grid-template-columns:1fr}.media-column{max-width:none}.lab-head{flex-direction:column}.mode-stack{justify-content:flex-start}.image-list{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:640px){.topbar{height:auto}.topbar-inner{padding:14px 18px;align-items:flex-start;flex-direction:column}.page{padding:14px 12px 24px}.workspace{min-height:0}.control-section,.lab-head,.composer,.runbar{padding-left:16px;padding-right:16px}.two-fields{grid-template-columns:1fr}.mode-stack{width:100%;flex-direction:column}.segmented{display:flex;width:100%}.segmented label{flex:1}.segmented span{justify-content:center}.input-action{grid-template-columns:1fr}.runbar{align-items:stretch;flex-direction:column}.run-button{width:100%}.result-head{align-items:flex-start;flex-direction:column;padding-left:16px;padding-right:16px}.tabs,.result-view{padding-left:16px;padding-right:16px}.image-list{grid-template-columns:repeat(2,minmax(0,1fr))}}
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
  <section class="control-section"><div class="section-title"><div><h2>运行配置</h2><p>CLI、后台服务与测试工作台共用</p></div></div><div class="two-fields"><div><label class="field-label" for="model">模型</label><select class="select" id="model"><option value="gpt-5.6-luna">gpt-5.6-luna</option></select></div><div><label class="field-label" for="effort">推理档位</label><select class="select" id="effort"></select></div></div><div class="two-fields"><div><label class="field-label" for="apiKey">本地 API Key</label><div class="input-action"><input class="field mono" id="apiKey" type="password" autocomplete="off"><button class="button compact" onclick="toggleKey(event)">显示</button></div></div><div><label class="field-label" for="port">端口</label><input class="field" id="port" type="number" min="1" max="65535"></div></div><label class="field-label" for="endpoint">当前 API 地址</label><div class="input-action"><input class="field mono" id="endpoint" readonly><button class="button compact" onclick="copyEndpoint()">复制</button></div><div class="actions config-actions"><button class="button" onclick="loadModels()">刷新模型</button><button class="button" onclick="copyKey()">复制 Key</button></div></section>
</aside>
<section class="panel lab">
  <div class="lab-head"><div class="lab-title"><h2>调用实验台</h2><p>验证订阅后端，或像第三方客户端一样请求本地 API</p></div><div class="mode-stack"><div id="testMode" class="segmented"><label><input type="radio" name="testMode" value="direct" checked><span>订阅直连</span></label><label><input type="radio" name="testMode" value="local_api"><span>本地 API</span></label></div><div id="apiFormat" class="segmented dim"><label><input type="radio" name="apiFormat" value="chat" checked><span>Chat Completions</span></label><label><input type="radio" name="apiFormat" value="responses"><span>Responses</span></label></div></div></div>
  <div class="composer"><div class="prompt-column"><label class="field-label" for="instructions">系统提示词</label><textarea class="textarea" id="instructions" placeholder="可选，例如：使用中文简洁回答。">使用中文简洁回答，不要解释系统配置。</textarea><label class="field-label" for="testText">用户输入</label><textarea class="textarea prompt" id="testText">请说明你接收到了哪些输入，并只回答关键信息。</textarea></div><div class="media-column"><label class="field-label">图片输入</label><div id="uploadZone" class="upload-zone"><div><strong>拖入图片或从本机选择</strong><div>最多 4 张，单张不超过 6 MB</div><label class="button compact" for="imageInput">选择图片</label><input id="imageInput" class="image-input" type="file" accept="image/*" multiple></div></div><div id="imageList" class="image-list"></div></div></div>
  <div class="runbar"><div id="runContext" class="run-context"><strong>订阅直连</strong><br>绕过本地 API，直接验证 OAuth 与模型后端</div><button id="runBtn" class="button primary run-button" onclick="runTest()">运行直连测试</button></div>
  <div class="result-area"><div class="result-head"><div class="metrics"><div><span>状态</span><strong id="metricStatus">等待</strong></div><div><span>耗时</span><strong id="metricDuration">-</strong></div><div><span>模型</span><strong id="metricModel">-</strong></div><div><span>图片</span><strong id="metricImages">0</strong></div></div></div><div class="tabs"><button class="tab active" data-view="output" onclick="showResult('output')">输出</button><button class="tab" data-view="response" onclick="showResult('response')">原始响应</button><button class="tab" data-view="request" onclick="showResult('request')">实际请求</button></div><pre id="testResult" class="result-view output empty">运行测试后，结果会显示在这里。</pre></div>
</section>
</div>
</main>
<script>
const $=id=>document.getElementById(id);let state=null,subscriptionModels=[],loadingModels=false,modelsAttempted=false,formInitialized=false,images=[],lastTest=null,resultView='output';
async function request(path,options={}){const response=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json','X-Codex-Dashboard':'1'},...options});const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data}
function notice(text,type='info'){$('notice').textContent=text;$('notice').className=`notice ${type==='error'?'error':type==='success'?'success':''}`.trim()}
function config(){return{host:'127.0.0.1',port:Number($('port').value),api_key:$('apiKey').value,model:$('model').value,reasoning_effort:$('effort').value}}
function selected(name){return document.querySelector(`input[name="${name}"]:checked`)?.value}
function setOptions(select,values,preferred){select.replaceChildren(...values.map(value=>{const option=document.createElement('option');option.value=value;option.textContent=value;return option}));select.value=values.includes(preferred)?preferred:(values[0]||'')}
function render(value){state=value;const auth=value.auth,server=value.server,c=value.config;const labels={running:'API 运行中',stopped:'API 已停止',key_mismatch:'API Key 不匹配',port_in_use:'端口被占用'};$('authText').textContent=auth.logged_in?(auth.expired?'登录已过期':'已登录'):'未登录';$('account').textContent=auth.logged_in?'OAuth 登录态可用':'';$('loginBtn').disabled=auth.logged_in&&!auth.expired;$('logoutBtn').disabled=!auth.logged_in;$('serverDot').className=server.running?'dot on':'dot';$('serverLabel').textContent=labels[server.status]||'API 状态未知';$('apiState').textContent=server.running?'运行中':labels[server.status]||'未知';$('serverMeta').textContent=server.running?`后台进程${server.pid?` PID ${server.pid}`:''}，关闭页面不会停止`:'仅监听本机，不向局域网开放';$('startBtn').disabled=server.status!=='stopped'||!auth.logged_in;$('stopBtn').disabled=server.status!=='running';if(!formInitialized){$('model').value=c.model;$('apiKey').value=c.api_key;$('port').value=c.port;setEffortOptions(c.reasoning_effort);formInitialized=true}syncEndpoint();syncTestMode()}
function setEffortOptions(preferred){const model=subscriptionModels.find(item=>item.slug===$('model').value);const efforts=model?.supported_reasoning_efforts?.length?model.supported_reasoning_efforts:(state?.reasoning_efforts||['low','medium','high']);const fallback=model?.default_reasoning_effort||efforts[0];setOptions($('effort'),efforts,efforts.includes(preferred)?preferred:fallback)}
function renderModels(preferred){const current=subscriptionModels.some(item=>item.slug===preferred)?preferred:subscriptionModels[0]?.slug;if(!current)return;setOptions($('model'),subscriptionModels.map(item=>item.slug),current);setEffortOptions($('effort').value)}
function syncEndpoint(){if(!state)return;const format=selected('apiFormat');const path=format==='responses'?'responses':'chat/completions';$('endpoint').value=`http://127.0.0.1:${$('port').value||state.config.port}/v1/${path}`}
function syncTestMode(){const mode=selected('testMode');const local=mode==='local_api';$('apiFormat').classList.toggle('dim',!local);$('apiFormat').querySelectorAll('input').forEach(input=>input.disabled=!local);const format=selected('apiFormat')==='responses'?'Responses':'Chat Completions';$('runContext').innerHTML=local?`<strong>本地 API · ${format}</strong><br>通过 Bearer Key 请求真实的 127.0.0.1 服务`:'<strong>订阅直连</strong><br>绕过本地 API，直接验证 OAuth 与模型后端';$('runBtn').textContent=local?'调用本地 API':'运行直连测试';$('runBtn').disabled=!state?.auth.logged_in||(local&&!state?.server.running);syncEndpoint()}
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
async function runTest(){const mode=selected('testMode');if(mode==='local_api'&&!state?.server.running){notice('请先启动本地 API 服务。','error');return}const button=$('runBtn');button.disabled=true;button.textContent='请求中...';$('testResult').textContent='正在发送请求，请稍候。';$('testResult').className='result-view output';$('metricStatus').textContent='请求中';const payload={mode,api_format:selected('apiFormat'),text:$('testText').value,instructions:$('instructions').value,model:$('model').value,reasoning_effort:$('effort').value,images};try{lastTest=await request('/api/test',{method:'POST',body:JSON.stringify(payload)});$('metricStatus').textContent=`HTTP ${lastTest.status}`;$('metricDuration').textContent=`${lastTest.duration_ms} ms`;$('metricModel').textContent=lastTest.response?.model||$('model').value;$('metricImages').textContent=String(lastTest.image_count);notice(mode==='local_api'?'本地 API 链路测试成功。':'订阅直连测试成功。','success');showResult('output')}catch(e){lastTest={error:e.message};$('metricStatus').textContent='失败';$('metricDuration').textContent='-';notice(e.message,'error');showResult('output')}finally{syncTestMode()}}
function showResult(view){resultView=view;document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.view===view));const box=$('testResult');box.className=`result-view ${view==='output'?'output':''}`;if(!lastTest){box.textContent='运行测试后，结果会显示在这里。';box.classList.add('empty');return}if(lastTest.error){box.textContent=lastTest.error;return}if(view==='output')box.textContent=lastTest.text||'模型没有返回文本，可在“原始响应”中查看结构化输出。';else if(view==='response')box.textContent=JSON.stringify(lastTest.response,null,2);else box.textContent=JSON.stringify({mode:lastTest.mode,endpoint:lastTest.endpoint,body:lastTest.request},null,2)}
async function copyValue(value,label){await navigator.clipboard.writeText(value);notice(`${label}已复制。`,'success')}
function copyEndpoint(){copyValue($('endpoint').value,'接口地址')}
function copyKey(){copyValue($('apiKey').value,'API Key')}
function toggleKey(event){const input=$('apiKey');input.type=input.type==='password'?'text':'password';event.currentTarget.textContent=input.type==='password'?'显示':'隐藏'}
$('model').addEventListener('change',()=>setEffortOptions($('effort').value));$('port').addEventListener('input',syncEndpoint);document.querySelectorAll('input[name="testMode"],input[name="apiFormat"]').forEach(input=>input.addEventListener('change',syncTestMode));$('imageInput').addEventListener('change',event=>addImages(event.target.files));const zone=$('uploadZone');['dragenter','dragover'].forEach(name=>zone.addEventListener(name,event=>{event.preventDefault();zone.classList.add('drag')}));['dragleave','drop'].forEach(name=>zone.addEventListener(name,event=>{event.preventDefault();zone.classList.remove('drag')}));zone.addEventListener('drop',event=>addImages(event.dataTransfer.files));refresh();setInterval(()=>refresh(true),5000);window.addEventListener('focus',()=>refresh(true));document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true)});
</script>
</body></html>'''
