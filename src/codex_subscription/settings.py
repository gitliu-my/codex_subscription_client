from __future__ import annotations

"""Shared local settings for terminal and dashboard entry points."""

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS_PATH = Path.home() / ".codex_subscription" / "settings.json"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MAX_CONCURRENCY = 10
REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_SETTINGS_PATH

    def defaults(self) -> dict[str, Any]:
        return {
            "host": "127.0.0.1",
            "port": 8317,
            "api_key": secrets.token_urlsafe(32),
            "model": DEFAULT_MODEL,
            "reasoning_effort": DEFAULT_REASONING_EFFORT,
            "max_concurrency": DEFAULT_MAX_CONCURRENCY,
        }

    def load(self) -> dict[str, Any]:
        defaults = self.defaults()
        if not self.path.exists():
            return defaults
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("settings must be an object")
            if loaded.get("api_key") in {None, "", "codex-local-translate"}:
                loaded["api_key"] = defaults["api_key"]
            return self.validate({**defaults, **loaded})
        except (OSError, ValueError, json.JSONDecodeError):
            return defaults

    def load_or_create(self) -> dict[str, Any]:
        return self.save(self.load())

    def update(self, **changes: Any) -> dict[str, Any]:
        return self.save({**self.load(), **changes})

    def save(self, value: dict[str, Any]) -> dict[str, Any]:
        config = self.validate(value)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        fd, temporary_name = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
        temporary_path = Path(temporary_name)
        try:
            os.chmod(temporary_path, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary_path, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return config

    def validate(self, value: dict[str, Any]) -> dict[str, Any]:
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
        effort = str(value.get("reasoning_effort") or DEFAULT_REASONING_EFFORT)
        if effort not in REASONING_EFFORTS:
            raise ValueError("不支持的推理档位。")
        api_key = str(value.get("api_key") or "").strip()
        if len(api_key) < 24:
            raise ValueError("本地 API Key 至少需要 24 个字符。")
        try:
            max_concurrency = int(
                value.get("max_concurrency", DEFAULT_MAX_CONCURRENCY)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("最大并发数必须是数字。") from exc
        if not 1 <= max_concurrency <= 32:
            raise ValueError("最大并发数必须在 1 到 32 之间。")
        return {
            "host": host,
            "port": port,
            "api_key": api_key,
            "model": model,
            "reasoning_effort": effort,
            "max_concurrency": max_concurrency,
        }
