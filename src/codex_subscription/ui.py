from __future__ import annotations

"""Local browser dashboard for authentication and API server management."""

import errno
import json
import http.cookies
import secrets
import threading
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

    def test(self, text: str) -> dict[str, str]:
        prompt = text.strip() or "只回答：连接成功"
        result = self._client(allow_login=False).generate(
            prompt,
            instructions="简洁回答用户，不要解释系统配置。",
        )
        return {"text": result}

    def api_url(self) -> str:
        return f"http://{self.config['host']}:{self.config['port']}/v1/chat/completions"

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
            "/api/test": lambda: self.server.controller.test(str(body.get("text") or "")),
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
        if length < 0 or length > 1024 * 1024:
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
            "style-src 'unsafe-inline'; connect-src 'self'; "
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
:root{color-scheme:light;--bg:#f5f7f8;--surface:#fff;--text:#172126;--muted:#627078;--line:#d8e0e4;--blue:#1666c5;--green:#18794e;--red:#c7352d;--focus:#a8cff8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:0}
header{background:#10181c;color:#fff;border-bottom:3px solid #33a06f}.bar{max-width:1040px;margin:auto;min-height:64px;padding:0 22px;display:flex;align-items:center;justify-content:space-between;gap:16px}
h1{font-size:18px;margin:0;font-weight:650}h2{font-size:15px;margin:0 0 16px}.sub{color:#aebac0;font-size:12px}.status{display:flex;align-items:center;gap:8px}.dot{width:9px;height:9px;border-radius:50%;background:#8b969b}.dot.on{background:#39b77a}
main{max-width:1040px;margin:24px auto;padding:0 22px 36px}.notice{min-height:42px;padding:10px 12px;margin-bottom:18px;border-left:3px solid var(--blue);background:#eaf3fd;color:#184f87}.notice.error{border-color:var(--red);background:#fff0ef;color:#87251f}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.panel{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:20px}.wide{grid-column:1/-1}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px}.actions{display:flex;gap:8px;flex-wrap:wrap}.meta{color:var(--muted);font-size:12px;margin-top:5px;overflow-wrap:anywhere}
label{display:block;color:#445158;font-size:12px;font-weight:600;margin:13px 0 5px}input,select,textarea{width:100%;min-height:38px;border:1px solid #b9c6cc;border-radius:4px;background:#fff;color:var(--text);padding:8px 10px;font:inherit;letter-spacing:0}textarea{min-height:86px;resize:vertical}input:focus,select:focus,textarea:focus{outline:3px solid var(--focus);border-color:var(--blue)}
.formgrid{display:grid;grid-template-columns:1fr 120px;gap:0 12px}.formgrid .full{grid-column:1/-1}button{min-height:36px;border:1px solid #9eabb1;border-radius:4px;background:#fff;color:#223038;padding:7px 13px;font:600 13px inherit;cursor:pointer;letter-spacing:0}button:hover{background:#f0f4f6}button.primary{background:var(--blue);border-color:var(--blue);color:#fff}button.stop{color:var(--red);border-color:#d9aaa6}button:disabled{opacity:.5;cursor:not-allowed}
.endpoint{display:grid;grid-template-columns:1fr auto;gap:8px}.endpoint input{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}.result{white-space:pre-wrap;background:#f3f6f7;border:1px solid var(--line);min-height:70px;padding:10px;margin-top:10px;border-radius:4px}.hidden{display:none}
@media(max-width:720px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}.bar{align-items:flex-start;padding-top:14px;padding-bottom:14px;flex-direction:column}.formgrid{grid-template-columns:1fr}.formgrid .full{grid-column:auto}.endpoint{grid-template-columns:1fr}.row{align-items:flex-start;flex-direction:column}}
</style>
</head>
<body>
<header><div class="bar"><div><h1>Codex Subscription</h1><div class="sub">本地 OAuth 与 OpenAI 兼容 API</div></div><div class="status"><span id="serverDot" class="dot"></span><span id="serverLabel">API 已停止</span></div></div></header>
<main>
<div id="notice" class="notice">正在读取本地状态...</div>
<div class="grid">
<section class="panel"><div class="row"><div><h2>ChatGPT 登录</h2><div id="authText">未登录</div><div id="account" class="meta"></div></div><div class="actions"><button id="loginBtn" class="primary" onclick="login()">网页登录</button><button id="logoutBtn" onclick="logout()">退出登录</button></div></div></section>
<section class="panel"><div class="row"><div><h2>API 服务</h2><div id="apiState">已停止</div><div id="serverMeta" class="meta">仅监听本机，不向局域网开放</div></div><div class="actions"><button id="startBtn" class="primary" onclick="startServer()">启动</button><button id="stopBtn" class="stop" onclick="stopServer()">停止</button></div></div></section>
<section class="panel wide"><h2>服务配置</h2><div class="formgrid"><div><label for="model">模型</label><select id="model"><option value="gpt-5.6-luna">gpt-5.6-luna</option></select></div><div><label for="effort">推理档位</label><select id="effort"></select></div><div><label for="apiKey">本地 API Key</label><input id="apiKey" autocomplete="off"></div><div><label for="port">端口</label><input id="port" type="number" min="1" max="65535"></div><div class="full"><label for="endpoint">Chat Completions 地址</label><div class="endpoint"><input id="endpoint" readonly><button onclick="copyEndpoint()">复制地址</button></div></div></div><div class="actions" style="margin-top:14px"><button onclick="loadModels()">刷新订阅模型</button><button onclick="copyKey()">复制 API Key</button></div></section>
<section class="panel wide"><h2>调用测试</h2><label for="testText">测试内容</label><textarea id="testText">只回答：连接成功</textarea><div class="actions" style="margin-top:10px"><button class="primary" onclick="runTest()">发送测试</button></div><div id="testResult" class="result">等待测试</div></section>
</div>
</main>
<script>
const $=id=>document.getElementById(id);let state=null,subscriptionModels=[],loadingModels=false,modelsAttempted=false,formInitialized=false;
async function request(path,options={}){const response=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json','X-Codex-Dashboard':'1'},...options});const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data}
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'notice error':'notice'}
function config(){return{host:'127.0.0.1',port:Number($('port').value),api_key:$('apiKey').value,model:$('model').value,reasoning_effort:$('effort').value}}
function render(value){state=value;const auth=value.auth,server=value.server,c=value.config;const labels={running:'API 运行中',stopped:'API 已停止',key_mismatch:'API Key 不匹配',port_in_use:'API 端口被占用'};$('authText').textContent=auth.logged_in?(auth.expired?'登录已过期':'已登录'):'未登录';$('account').textContent=auth.logged_in?'登录凭据仅保存在本机':'';$('loginBtn').disabled=auth.logged_in&&!auth.expired;$('logoutBtn').disabled=!auth.logged_in;$('serverDot').className=server.running?'dot on':'dot';$('serverLabel').textContent=labels[server.status]||'API 状态未知';$('apiState').textContent=server.running?'运行中（后台服务）':labels[server.status]||'状态未知';$('serverMeta').textContent=server.running?'关闭管理页不会停止 API':'仅监听本机，不向局域网开放';$('startBtn').disabled=server.status!=='stopped'||!auth.logged_in;$('stopBtn').disabled=server.status!=='running';$('endpoint').value=server.url;if(!formInitialized){$('model').value=c.model;$('apiKey').value=c.api_key;$('port').value=c.port;setEffortOptions(c.reasoning_effort);formInitialized=true}}
function setEffortOptions(preferred){const selected=subscriptionModels.find(m=>m.slug===$('model').value);const efforts=selected?.supported_reasoning_efforts?.length?selected.supported_reasoning_efforts:state.reasoning_efforts;const fallback=selected?.default_reasoning_effort||efforts[0];const value=efforts.includes(preferred)?preferred:fallback;$('effort').innerHTML=efforts.map(x=>`<option value="${x}">${x}</option>`).join('');$('effort').value=value}
function renderModels(preferred){const current=subscriptionModels.some(m=>m.slug===preferred)?preferred:subscriptionModels[0]?.slug;if(!current)return;$('model').innerHTML=subscriptionModels.map(m=>`<option value="${m.slug}">${m.slug}</option>`).join('');$('model').value=current;setEffortOptions($('effort').value)}
async function refresh(silent=false){try{const value=await request('/api/state');render(value);if(value.auth.logged_in&&!modelsAttempted)await loadModels(true);if(!silent)notice('本地状态已就绪。')}catch(e){if(!silent)notice(e.message,true)}}
async function action(label,path,body={}){notice(`${label}...`);try{const data=await request(path,{method:'POST',body:JSON.stringify(body)});if(data.auth)render(data);notice(`${label}完成。`);return data}catch(e){notice(e.message,true);throw e}}
async function login(){await action('等待浏览器授权','/api/login');modelsAttempted=false;await loadModels()}
async function logout(){await action('退出登录','/api/logout');subscriptionModels=[];modelsAttempted=false}
async function startServer(){await action('启动 API','/api/server/start',config())}
async function stopServer(){await action('停止 API','/api/server/stop')}
async function loadModels(silent=false){if(loadingModels)return;loadingModels=true;modelsAttempted=true;if(!silent)notice('正在查询订阅模型...');try{const preferred=$('model').value||state.config.model;subscriptionModels=await request('/api/models');renderModels(preferred);if(!silent)notice(`已加载 ${subscriptionModels.length} 个模型。`)}catch(e){if(!silent)notice(e.message,true)}finally{loadingModels=false}}
async function runTest(){const box=$('testResult');box.textContent='请求中...';try{const data=await action('模型调用','/api/test',{text:$('testText').value});box.textContent=data.text}catch(e){box.textContent=e.message}}
async function copyValue(value,label){await navigator.clipboard.writeText(value);notice(`${label}已复制。`)}
function copyEndpoint(){copyValue($('endpoint').value,'接口地址')}
function copyKey(){copyValue($('apiKey').value,'API Key')}
$('model').addEventListener('change',()=>setEffortOptions($('effort').value));refresh();setInterval(()=>refresh(true),5000);window.addEventListener('focus',()=>refresh(true));document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true)});
</script>
</body></html>'''
