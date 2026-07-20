from __future__ import annotations

"""Lifecycle management for the shared local API process."""

import http.client
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LOG_PATH = Path.home() / ".codex_subscription" / "api.log"


@dataclass(frozen=True)
class ApiServiceStatus:
    state: str
    pid: int | None = None


def probe_api(config: dict[str, Any], timeout: float = 0.4) -> ApiServiceStatus:
    connection = http.client.HTTPConnection(
        str(config["host"]), int(config["port"]), timeout=timeout
    )
    try:
        connection.request(
            "GET",
            "/__csub/status",
            headers={"Authorization": f"Bearer {config['api_key']}"},
        )
        response = connection.getresponse()
        status_code = response.status
        body = json.loads(response.read().decode("utf-8"))
    except OSError:
        return ApiServiceStatus("stopped")
    except (http.client.HTTPException, ValueError, json.JSONDecodeError):
        return ApiServiceStatus("port_in_use")
    finally:
        connection.close()

    if status_code == 200 and isinstance(body, dict) and body.get("status") == "running":
        pid = body.get("pid")
        return ApiServiceStatus("running", pid if isinstance(pid, int) else None)

    error = body.get("error") if isinstance(body, dict) else None
    is_csub = isinstance(error, dict) and error.get("type") == "codex_subscription_error"
    if is_csub and status_code == 404:
        return ApiServiceStatus("running")
    if is_csub and status_code in {401, 403}:
        return ApiServiceStatus("key_mismatch")
    return ApiServiceStatus("port_in_use")


def start_api_service(
    config: dict[str, Any],
    timeout: float = 20,
    log_path: Path = DEFAULT_LOG_PATH,
) -> tuple[bool, ApiServiceStatus]:
    current = probe_api(config)
    if current.state == "running":
        return False, current
    _raise_unstartable(current, config)

    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(log_path.parent, 0o700)
    with log_path.open("ab") as log:
        os.chmod(log_path, 0o600)
        process = subprocess.Popen(
            _serve_command(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env={**os.environ, "CSUB_BACKGROUND": "1"},
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = probe_api(config)
        if status.state == "running":
            return True, status
        if process.poll() is not None:
            raise ValueError(f"后台 API 启动失败，请查看日志：{log_path}")
        time.sleep(0.1)

    process.terminate()
    raise ValueError(f"后台 API 启动超时，请查看日志：{log_path}")


def stop_api_service(
    config: dict[str, Any], timeout: float = 5
) -> tuple[bool, ApiServiceStatus]:
    current = probe_api(config)
    if current.state == "stopped":
        return False, current
    if current.state == "key_mismatch":
        raise ValueError("API 正在运行，但当前保存的 API Key 不匹配，无法安全停止。")
    if current.state == "port_in_use":
        raise ValueError(f"端口 {config['port']} 由其他程序占用，无法通过 csub 停止。")

    connection = http.client.HTTPConnection(
        str(config["host"]), int(config["port"]), timeout=1
    )
    try:
        connection.request(
            "POST",
            "/__csub/stop",
            body=b"",
            headers={"Authorization": f"Bearer {config['api_key']}"},
        )
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise ValueError(f"停止 API 失败：HTTP {response.status}")
    except OSError as exc:
        raise ValueError(f"停止 API 失败：{exc}") from exc
    finally:
        connection.close()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = probe_api(config)
        if status.state == "stopped":
            return True, status
        time.sleep(0.05)
    raise ValueError("API 已收到停止请求，但未在预期时间内退出。")


def restart_api_service(config: dict[str, Any]) -> ApiServiceStatus:
    stop_api_service(config)
    return start_api_service(config)[1]


def _serve_command() -> list[str]:
    if getattr(sys, "frozen", False) and Path(sys.executable).name == "csub":
        return [sys.executable, "serve", "--no-login"]
    installed = Path.home() / ".local" / "lib" / "csub" / "csub"
    if installed.is_file() and os.access(installed, os.X_OK):
        return [str(installed), "serve", "--no-login"]
    return [sys.executable, "-m", "codex_subscription", "serve", "--no-login"]


def _raise_unstartable(status: ApiServiceStatus, config: dict[str, Any]) -> None:
    if status.state == "key_mismatch":
        raise ValueError(
            "已有 csub API 使用不同的 Key 运行；请先结束旧进程或恢复对应 Key。"
        )
    if status.state == "port_in_use":
        raise ValueError(f"端口 {config['port']} 已被其他程序占用。")
