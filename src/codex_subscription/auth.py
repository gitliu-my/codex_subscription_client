from __future__ import annotations

"""ChatGPT/Codex OAuth login, token storage, and token refresh."""

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .transport import urlopen


JWT_AUTH_CLAIM = "https://api.openai.com/auth"


class CodexOAuthError(RuntimeError):
    """Raised when browser login, token parsing, or token refresh fails."""


@dataclass(frozen=True)
class CodexOAuthConfig:
    client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    authorize_url: str = "https://auth.openai.com/oauth/authorize"
    token_url: str = "https://auth.openai.com/oauth/token"
    redirect_uri: str = "http://localhost:1455/auth/callback"
    scope: str = "openid profile email offline_access"
    callback_host: str = "127.0.0.1"
    callback_port: int = 1455


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_at: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthTokens:
        try:
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                expires_at=int(data["expires_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CodexOAuthError("Codex OAuth token 文件格式无效，请重新登录。") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "oauth",
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return self.expires_at <= int(time.time()) + skew_seconds


@dataclass(frozen=True)
class AuthStatus:
    logged_in: bool
    expired: bool
    token_path: Path
    account_id: str | None = None


class FileTokenStore:
    """JSON token store with user-only permissions and atomic replacement."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or default_token_path()).expanduser()

    def load(self) -> OAuthTokens | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexOAuthError(f"无法读取 token 文件：{self.path}") from exc
        return OAuthTokens.from_dict(data)

    def save(self, tokens: OAuthTokens) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)

        fd, temporary_name = tempfile.mkstemp(prefix=".auth-", dir=self.path.parent)
        temporary_path = Path(temporary_name)
        try:
            os.chmod(temporary_path, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(tokens.to_dict(), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class CodexOAuth:
    """Reusable Codex browser OAuth manager."""

    def __init__(
        self,
        store: FileTokenStore | None = None,
        config: CodexOAuthConfig | None = None,
        notifier: Callable[[str], None] | None = None,
        browser_opener: Callable[[str], object] | None = None,
    ) -> None:
        self.store = store or FileTokenStore()
        self.config = config or CodexOAuthConfig()
        self.notifier = notifier or print
        self.browser_opener = browser_opener or webbrowser.open

    def status(self) -> AuthStatus:
        tokens = self.store.load()
        if tokens is None:
            return AuthStatus(False, False, self.store.path)
        account_id: str | None = None
        try:
            account_id = extract_chatgpt_account_id(tokens.access_token)
        except CodexOAuthError:
            pass
        return AuthStatus(True, tokens.is_expired(), self.store.path, account_id)

    def login(self, timeout_seconds: int = 600, open_browser: bool = True) -> OAuthTokens:
        verifier, challenge = create_pkce_pair()
        state = secrets.token_hex(16)
        authorization_url = self.build_authorization_url(challenge, state)

        try:
            callback = _OAuthCallbackServer(self.config, state)
        except OSError as exc:
            raise CodexOAuthError(
                f"无法监听 {self.config.redirect_uri}：{exc}。请关闭占用 1455 端口的程序后重试。"
            ) from exc

        try:
            self.notifier("请在浏览器中完成 ChatGPT/Codex 授权：")
            self.notifier(authorization_url)
            if open_browser:
                self.browser_opener(authorization_url)
            code = callback.wait_for_code(timeout_seconds)
        finally:
            callback.close()

        tokens = self._request_tokens(
            {
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": self.config.redirect_uri,
            }
        )
        self.store.save(tokens)
        return tokens

    def get_access_token(self, allow_login: bool = True) -> str:
        tokens = self.store.load()
        if tokens and not tokens.is_expired():
            return tokens.access_token

        if tokens:
            try:
                refreshed = self.refresh(tokens.refresh_token)
            except CodexOAuthError:
                if not allow_login:
                    raise
            else:
                return refreshed.access_token

        if not allow_login:
            raise CodexOAuthError("没有可用的 Codex OAuth 登录态，请先执行网页登录。")
        return self.login(open_browser=_env_bool("CODEX_SUBSCRIPTION_OPEN_BROWSER", True)).access_token

    def refresh(self, refresh_token: str | None = None) -> OAuthTokens:
        existing = self.store.load()
        token = refresh_token or (existing.refresh_token if existing else None)
        if not token:
            raise CodexOAuthError("没有 refresh token，请重新登录。")

        refreshed = self._request_tokens(
            {
                "grant_type": "refresh_token",
                "refresh_token": token,
                "client_id": self.config.client_id,
            },
            fallback_refresh_token=token,
        )
        self.store.save(refreshed)
        return refreshed

    def logout(self) -> None:
        self.store.clear()

    def build_authorization_url(self, challenge: str, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": self.config.scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli_rs",
        }
        return f"{self.config.authorize_url}?{urllib.parse.urlencode(params)}"

    def _request_tokens(
        self,
        payload: dict[str, str],
        fallback_refresh_token: str | None = None,
    ) -> OAuthTokens:
        request = urllib.request.Request(
            self.config.token_url,
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CodexOAuthError(f"OAuth token 请求失败，HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CodexOAuthError(f"OAuth token 请求失败：{exc}") from exc

        access = body.get("access_token")
        refresh = body.get("refresh_token") or fallback_refresh_token
        expires_in = body.get("expires_in")
        if not access or not refresh or not isinstance(expires_in, (int, float)):
            raise CodexOAuthError("OAuth token 返回缺少 access_token、refresh_token 或 expires_in。")

        return OAuthTokens(
            access_token=str(access),
            refresh_token=str(refresh),
            expires_at=int(time.time()) + int(expires_in),
        )


class _OAuthCallbackServer:
    def __init__(self, config: CodexOAuthConfig, expected_state: str) -> None:
        self._event = threading.Event()
        self._code: str | None = None
        self._error: str | None = None
        parent = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/auth/callback":
                    self._send(404, "Not found")
                    return

                params = urllib.parse.parse_qs(parsed.query)
                state = _first(params.get("state"))
                code = _first(params.get("code"))
                error = _first(params.get("error"))

                if state != expected_state:
                    parent._error = "OAuth state 不匹配，已拒绝回调。"
                    self._send(400, "State mismatch")
                elif error:
                    parent._error = error
                    self._send(400, error)
                elif not code:
                    parent._error = "OAuth 回调缺少 code。"
                    self._send(400, "Missing code")
                else:
                    parent._code = code
                    self._send(
                        200,
                        "<html><body><h1>Login complete</h1>"
                        "<p>You can close this tab and return to the terminal.</p>"
                        "</body></html>",
                        content_type="text/html; charset=utf-8",
                    )
                parent._event.set()

            def _send(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer(
            (config.callback_host, config.callback_port), CallbackHandler
        )
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def wait_for_code(self, timeout_seconds: int) -> str:
        if not self._event.wait(timeout_seconds):
            raise CodexOAuthError("等待浏览器 OAuth 回调超时。")
        if self._error:
            raise CodexOAuthError(self._error)
        if not self._code:
            raise CodexOAuthError("OAuth 回调没有返回授权码。")
        return self._code

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


def default_token_path() -> Path:
    configured = os.getenv("CODEX_SUBSCRIPTION_TOKEN_FILE")
    if configured:
        return Path(configured)
    return Path.home() / ".codex_subscription" / "auth.json"


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise CodexOAuthError("access token 不是有效 JWT。")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexOAuthError("无法解析 access token payload。") from exc
    if not isinstance(payload, dict):
        raise CodexOAuthError("access token payload 格式异常。")
    return payload


def extract_chatgpt_account_id(access_token: str) -> str:
    auth_claim = decode_jwt_payload(access_token).get(JWT_AUTH_CLAIM)
    if not isinstance(auth_claim, dict) or not auth_claim.get("chatgpt_account_id"):
        raise CodexOAuthError("access token 中没有 chatgpt_account_id。")
    return str(auth_claim["chatgpt_account_id"])


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None
