from __future__ import annotations

"""Lifecycle management for the shared local API process."""

import http.client
import json
import os
import plistlib
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LOG_PATH = Path.home() / ".codex_subscription" / "api.log"
MACOS_LAUNCH_AGENT_LABEL = "com.gitliu-my.csub-api"
MACOS_LAUNCH_AGENT_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LAUNCH_AGENT_LABEL}.plist"
)


@dataclass(frozen=True)
class ApiServiceStatus:
    state: str
    pid: int | None = None


@dataclass(frozen=True)
class _MacOSLaunchAgent:
    path: Path
    label: str
    loaded: bool
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

    launch_agent = _detect_macos_launch_agent()
    process: subprocess.Popen[bytes] | None = None
    if launch_agent is not None:
        if launch_agent.loaded:
            raise ValueError(
                "csub 的 macOS 后台守护任务已加载，但 API 未就绪；"
                f"请查看日志：{log_path}"
            )
        _bootstrap_macos_launch_agent(launch_agent)
    else:
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
        if process is not None and process.poll() is not None:
            raise ValueError(f"后台 API 启动失败，请查看日志：{log_path}")
        time.sleep(0.1)

    if launch_agent is not None:
        loaded_agent = _detect_macos_launch_agent()
        if loaded_agent is not None and loaded_agent.loaded:
            _bootout_macos_launch_agent(loaded_agent)
    elif process is not None:
        process.terminate()
    raise ValueError(f"后台 API 启动超时，请查看日志：{log_path}")


def stop_api_service(
    config: dict[str, Any], timeout: float = 5, settle_time: float = 0.25
) -> tuple[bool, ApiServiceStatus]:
    current = probe_api(config)
    launch_agent = _detect_macos_launch_agent()
    if launch_agent is not None and launch_agent.loaded:
        _bootout_macos_launch_agent(launch_agent)
        status = _wait_until_stopped(config, timeout, settle_time)
        if status.state == "stopped":
            return True, status
        current = status

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

    status = _wait_until_stopped(config, timeout, settle_time)
    if status.state == "stopped":
        return True, status
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


def _wait_until_stopped(
    config: dict[str, Any], timeout: float, settle_time: float
) -> ApiServiceStatus:
    deadline = time.monotonic() + timeout
    stopped_since: float | None = None
    last = probe_api(config)
    while time.monotonic() < deadline:
        if last.state == "stopped":
            if stopped_since is None:
                stopped_since = time.monotonic()
            if time.monotonic() - stopped_since >= settle_time:
                return last
        else:
            stopped_since = None
        time.sleep(0.05)
        last = probe_api(config)
    return last


def _detect_macos_launch_agent() -> _MacOSLaunchAgent | None:
    if sys.platform != "darwin" or not MACOS_LAUNCH_AGENT_PATH.is_file():
        return None
    try:
        with MACOS_LAUNCH_AGENT_PATH.open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return None
    if not isinstance(payload, dict):
        return None

    arguments = payload.get("ProgramArguments")
    if not isinstance(arguments, list) or not arguments:
        return None
    executable = arguments[0]
    if (
        payload.get("Label") != MACOS_LAUNCH_AGENT_LABEL
        or not isinstance(executable, str)
        or Path(executable).name != "csub"
        or "serve" not in arguments[1:]
    ):
        return None

    loaded, pid = _launch_agent_load_state(MACOS_LAUNCH_AGENT_LABEL)
    return _MacOSLaunchAgent(
        path=MACOS_LAUNCH_AGENT_PATH,
        label=MACOS_LAUNCH_AGENT_LABEL,
        loaded=loaded,
        pid=pid,
    )


def _launch_agent_load_state(label: str) -> tuple[bool, int | None]:
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, None
    for line in result.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3 or fields[2] != label:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            pid = None
        return True, pid
    return False, None


def _bootstrap_macos_launch_agent(agent: _MacOSLaunchAgent) -> None:
    _run_launchctl(
        ["bootstrap", f"gui/{os.getuid()}", str(agent.path)],
        "加载 macOS 后台守护任务失败",
    )


def _bootout_macos_launch_agent(agent: _MacOSLaunchAgent) -> None:
    _run_launchctl(
        ["bootout", f"gui/{os.getuid()}/{agent.label}"],
        "停止 macOS 后台守护任务失败",
    )


def _run_launchctl(arguments: list[str], error_message: str) -> None:
    try:
        result = subprocess.run(
            ["launchctl", *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{error_message}：{exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise ValueError(f"{error_message}{f'：{detail}' if detail else ''}")


def _raise_unstartable(status: ApiServiceStatus, config: dict[str, Any]) -> None:
    if status.state == "key_mismatch":
        raise ValueError(
            "已有 csub API 使用不同的 Key 运行；请先结束旧进程或恢复对应 Key。"
        )
    if status.state == "port_in_use":
        raise ValueError(f"端口 {config['port']} 已被其他程序占用。")
