from __future__ import annotations

"""Local browser dashboard for authentication and API server management."""

import json
import http.client
import os
import secrets
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .auth import CodexOAuth, CodexOAuthError
from .client import CodexBackendError, CodexSubscriptionClient
from .server import SubscriptionApiServer


DEFAULT_SETTINGS_PATH = Path.home() / ".codex_subscription" / "settings.json"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


class DashboardController:
    def __init__(self, settings_path: Path | None = None) -> None:
        self.settings_path = settings_path or DEFAULT_SETTINGS_PATH
        self.auth = CodexOAuth()
        self._lock = threading.RLock()
        self._api_server: SubscriptionApiServer | None = None
        self._api_thread: threading.Thread | None = None
        self.config = self._load_settings()

    def state(self) -> dict[str, Any]:
        status = self.auth.status()
        with self._lock:
            managed = self._api_server is not None
        external = False if managed else self._external_api_is_healthy()
        port_in_use = managed or external or self._api_port_is_open()
        return {
            "auth": {
                "logged_in": status.logged_in,
                "expired": status.expired,
                "account_id": status.account_id,
                "token_path": str(status.token_path),
            },
            "server": {
                "running": managed or external,
                "managed": managed,
                "external": external,
                "port_in_use": port_in_use,
                "url": self.api_url(),
            },
            "config": dict(self.config),
            "reasoning_efforts": list(REASONING_EFFORTS),
        }

    def login(self) -> dict[str, Any]:
        self.auth.login()
        return self.state()

    def logout(self) -> dict[str, Any]:
        self.stop_api()
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
        self.stop_api()
        client = self._client(config, allow_login=False)
        server = SubscriptionApiServer(
            (config["host"], config["port"]), client, api_key=config["api_key"] or None
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with self._lock:
            self.config = config
            self._api_server = server
            self._api_thread = thread
            self._save_settings()
        thread.start()
        return self.state()

    def stop_api(self) -> dict[str, Any]:
        with self._lock:
            server = self._api_server
            thread = self._api_thread
            self._api_server = None
            self._api_thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
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

    def _external_api_is_healthy(self) -> bool:
        headers = {}
        if self.config["api_key"]:
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        connection = http.client.HTTPConnection(
            self.config["host"], self.config["port"], timeout=0.5
        )
        try:
            connection.request("GET", "/health", headers=headers)
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and body.get("status") == "ok"
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        finally:
            connection.close()

    def _api_port_is_open(self) -> bool:
        connection = http.client.HTTPConnection(
            self.config["host"], self.config["port"], timeout=0.3
        )
        try:
            connection.connect()
            return True
        except OSError:
            return False
        finally:
            connection.close()

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
        defaults: dict[str, Any] = {
            "host": "127.0.0.1",
            "port": 8317,
            "api_key": "codex-local-translate",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "low",
        }
        if not self.settings_path.exists():
            return defaults
        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return self._validated_config({**defaults, **loaded})
        except (OSError, ValueError, json.JSONDecodeError):
            return defaults

    def _save_settings(self) -> None:
        self.settings_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.settings_path.parent, 0o700)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".settings-", dir=self.settings_path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.chmod(temporary_path, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.config, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary_path, self.settings_path)
            os.chmod(self.settings_path, 0o600)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def _validated_config(self, value: dict[str, Any]) -> dict[str, Any]:
        host = str(value.get("host") or "127.0.0.1")
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("管理界面只允许 API 监听本机 127.0.0.1。")
        try:
            port = int(value.get("port", 8317))
        except (TypeError, ValueError) as exc:
            raise ValueError("端口必须是数字。") from exc
        if not 1 <= port <= 65535:
            raise ValueError("端口必须在 1 到 65535 之间。")
        model = str(value.get("model") or "").strip()
        if not model:
            raise ValueError("模型名称不能为空。")
        effort = str(value.get("reasoning_effort") or "low")
        if effort not in REASONING_EFFORTS:
            raise ValueError("不支持的推理档位。")
        return {
            "host": host,
            "port": port,
            "api_key": str(value.get("api_key") or "").strip(),
            "model": model,
            "reasoning_effort": effort,
        }


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: DashboardController) -> None:
        super().__init__(address, DashboardHandler)
        self.controller = controller


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(200, DASHBOARD_HTML, "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(200, self.server.controller.state())
        elif path == "/api/models":
            self._call(self.server.controller.models)
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
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
            self.server.controller.stop_api()
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
        if length > 1024 * 1024:
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

    def _send(self, status: int, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

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
    except OSError:
        if _dashboard_is_running(url):
            print(f"Codex Subscription 管理界面已在运行：{url}")
            if open_browser:
                webbrowser.open(url)
            return
        raise
    print(f"Codex Subscription 管理界面：{url}")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.controller.stop_api()
        server.server_close()


def _dashboard_is_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/state", timeout=2) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and {"auth", "server", "config"} <= value.keys()


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
async function request(path,options={}){const response=await fetch(path,{headers:{'Content-Type':'application/json'},...options});const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data}
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'notice error':'notice'}
function config(){return{host:'127.0.0.1',port:Number($('port').value),api_key:$('apiKey').value,model:$('model').value,reasoning_effort:$('effort').value}}
function render(value){state=value;const auth=value.auth,server=value.server,c=value.config;$('authText').textContent=auth.logged_in?(auth.expired?'登录已过期':'已登录'):'未登录';$('account').textContent=auth.account_id?`账号：${auth.account_id}`:'';$('loginBtn').disabled=auth.logged_in&&!auth.expired;$('logoutBtn').disabled=!auth.logged_in;$('serverDot').className=server.running?'dot on':'dot';$('serverLabel').textContent=server.external?'API 由终端运行':server.managed?'API 由 App 运行':server.port_in_use?'API 端口被占用':'API 已停止';$('apiState').textContent=server.external?'运行中（终端管理）':server.managed?'运行中（App 管理）':server.port_in_use?'端口被其他程序占用':'已停止';$('serverMeta').textContent=server.external?'参数由终端启动命令决定，请在终端停止':server.managed?'由 App 管理，仅监听本机':'仅监听本机，不向局域网开放';$('startBtn').disabled=server.port_in_use||!auth.logged_in;$('stopBtn').disabled=!server.managed;$('endpoint').value=server.url;if(!formInitialized){$('model').value=c.model;$('apiKey').value=c.api_key;$('port').value=c.port;setEffortOptions(c.reasoning_effort);formInitialized=true}}
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
