from __future__ import annotations

"""Application API key metadata and recoverable macOS Keychain secrets."""

import hashlib
import json
import os
import pty
import select
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol


DEFAULT_API_KEYS_PATH = Path.home() / ".codex_subscription" / "api_keys.db"
KEYCHAIN_SERVICE = "com.gitliu-my.csub.api-key"
ApiKeyPermissions = dict[str, tuple[str, ...]]


class SecretStore(Protocol):
    def put(self, account: str, secret: str) -> None: ...

    def get(self, account: str) -> str: ...

    def delete(self, account: str) -> None: ...


class MacOSKeychainSecretStore:
    def __init__(self, service: str = KEYCHAIN_SERVICE) -> None:
        if sys.platform != "darwin":
            raise ValueError("API Key 密钥存储目前只支持 macOS Keychain。")
        self.service = service
        self.security = "/usr/bin/security"

    def put(self, account: str, secret: str) -> None:
        result = _run_with_tty_input(
            [
                self.security,
                "add-generic-password",
                "-U",
                "-a",
                account,
                "-s",
                self.service,
                "-l",
                "csub application API key",
                "-w",
            ],
            secret,
        )
        if result.returncode != 0:
            raise ValueError(
                f"无法将 API Key 写入 macOS Keychain：{_command_error(result)}"
            )

    def get(self, account: str) -> str:
        result = subprocess.run(
            [
                self.security,
                "find-generic-password",
                "-a",
                account,
                "-s",
                self.service,
                "-w",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(
                "macOS Keychain 中未找到该 API Key，请删除后重新创建。"
            )
        secret = result.stdout.rstrip("\r\n")
        if not secret:
            raise ValueError("macOS Keychain 返回了空的 API Key。")
        return secret

    def delete(self, account: str) -> None:
        result = subprocess.run(
            [
                self.security,
                "delete-generic-password",
                "-a",
                account,
                "-s",
                self.service,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode not in {0, 44}:
            raise ValueError(
                f"无法从 macOS Keychain 删除 API Key：{_command_error(result)}"
            )


class MemorySecretStore:
    """Small injectable secret backend used by tests and embedders."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, account: str, secret: str) -> None:
        self.values[account] = secret

    def get(self, account: str) -> str:
        try:
            return self.values[account]
        except KeyError as exc:
            raise ValueError("密钥不存在。") from exc

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    name: str
    prefix: str
    enabled: bool
    is_system: bool
    permissions: ApiKeyPermissions | None
    created_at: str
    updated_at: str
    last_used_at: str | None
    request_count: int

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "prefix": self.prefix,
            "masked_key": f"{self.prefix}_••••••••",
            "enabled": self.enabled,
            "is_system": self.is_system,
            "permissions": (
                None
                if self.permissions is None
                else {
                    model: list(efforts)
                    for model, efforts in self.permissions.items()
                }
            ),
            "unrestricted": self.permissions is None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "request_count": self.request_count,
        }

    def allows(self, model: str, reasoning_effort: str) -> bool:
        if self.permissions is None:
            return True
        return reasoning_effort in self.permissions.get(model, ())


class ApiKeyStore:
    def __init__(
        self,
        path: Path = DEFAULT_API_KEYS_PATH,
        secret_store: SecretStore | None = None,
    ) -> None:
        self.path = path
        self.secret_store = secret_store or MacOSKeychainSecretStore()
        self._initialize()

    def ensure_legacy_key(
        self, secret: str, name: str = "默认兼容 Key"
    ) -> ApiKeyRecord:
        secret = _validate_secret(secret)
        fingerprint = _fingerprint(secret)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM api_keys WHERE secret_hash = ?", (fingerprint,)
            ).fetchone()
        if row is not None:
            record = _record(row)
            if record.is_system:
                try:
                    stored_secret = self.secret_store.get(record.id)
                except ValueError:
                    stored_secret = None
                if stored_secret != secret:
                    self.secret_store.put(record.id, secret)
            return record

        key_id = f"system-{fingerprint[:24]}"
        prefix = f"csub_legacy_{fingerprint[:8]}"
        created_at = _now()
        self.secret_store.put(key_id, secret)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO api_keys (
                        id, name, prefix, secret_hash, enabled, is_system,
                        permissions_json, created_at, updated_at, last_used_at,
                        request_count
                    ) VALUES (?, ?, ?, ?, 1, 1, NULL, ?, ?, NULL, 0)
                    """,
                    (
                        key_id,
                        self._available_name(connection, name),
                        prefix,
                        fingerprint,
                        created_at,
                        created_at,
                    ),
                )
        except Exception:
            self.secret_store.delete(key_id)
            raise
        return self.get(key_id)

    def create(
        self,
        name: str,
        permissions: object = None,
    ) -> tuple[ApiKeyRecord, str]:
        name = _validate_name(name)
        normalized_permissions = _normalize_permissions(permissions)
        key_id = uuid.uuid4().hex
        prefix = f"csub_live_{key_id[:8]}"
        secret = f"{prefix}_{secrets.token_urlsafe(32)}"
        fingerprint = _fingerprint(secret)
        created_at = _now()
        self.secret_store.put(key_id, secret)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO api_keys (
                        id, name, prefix, secret_hash, enabled, is_system,
                        permissions_json, created_at, updated_at, last_used_at,
                        request_count
                    ) VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?, NULL, 0)
                    """,
                    (
                        key_id,
                        name,
                        prefix,
                        fingerprint,
                        _encode_permissions(normalized_permissions),
                        created_at,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            self.secret_store.delete(key_id)
            raise ValueError(f"API Key 名称已存在：{name}") from exc
        except Exception:
            self.secret_store.delete(key_id)
            raise
        return self.get(key_id), secret

    def list(self) -> list[ApiKeyRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM api_keys ORDER BY is_system DESC, created_at ASC"
            ).fetchall()
        return [_record(row) for row in rows]

    def list_public(self) -> list[dict[str, object]]:
        return [record.public() for record in self.list()]

    def get(self, selector: str) -> ApiKeyRecord:
        return self._resolve(selector)

    def reveal(self, selector: str) -> str:
        record = self._resolve(selector)
        secret = self.secret_store.get(record.id)
        if not secrets.compare_digest(_fingerprint(secret), self._secret_hash(record.id)):
            raise ValueError("macOS Keychain 中的密钥与本地元数据不匹配。")
        return secret

    def rename(self, selector: str, name: str) -> ApiKeyRecord:
        record = self._resolve(selector)
        name = _validate_name(name)
        try:
            with self._connection() as connection:
                connection.execute(
                    "UPDATE api_keys SET name = ?, updated_at = ? WHERE id = ?",
                    (name, _now(), record.id),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"API Key 名称已存在：{name}") from exc
        return self.get(record.id)

    def set_enabled(self, selector: str, enabled: bool) -> ApiKeyRecord:
        record = self._resolve(selector)
        if record.is_system and not enabled:
            raise ValueError("默认兼容 Key 用于服务控制和升级兼容，不能禁用。")
        with self._connection() as connection:
            connection.execute(
                "UPDATE api_keys SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, _now(), record.id),
            )
        return self.get(record.id)

    def set_permissions(
        self,
        selector: str,
        permissions: object,
    ) -> ApiKeyRecord:
        record = self._resolve(selector)
        if record.is_system:
            raise ValueError("默认兼容 Key 固定拥有全部模型权限。")
        normalized = _normalize_permissions(permissions)
        with self._connection() as connection:
            connection.execute(
                "UPDATE api_keys SET permissions_json = ?, updated_at = ? WHERE id = ?",
                (_encode_permissions(normalized), _now(), record.id),
            )
        return self.get(record.id)

    def delete(self, selector: str) -> None:
        record = self._resolve(selector)
        if record.is_system:
            raise ValueError("默认兼容 Key 不能删除。")
        self.secret_store.delete(record.id)
        with self._connection() as connection:
            connection.execute("DELETE FROM api_keys WHERE id = ?", (record.id,))

    def authenticate(self, secret: str) -> ApiKeyRecord | None:
        if not secret:
            return None
        fingerprint = _fingerprint(secret)
        used_at = _now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM api_keys WHERE secret_hash = ? AND enabled = 1",
                (fingerprint,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE api_keys
                SET last_used_at = ?, request_count = request_count + 1
                WHERE id = ?
                """,
                (used_at, row["id"]),
            )
            updated = dict(row)
            updated["last_used_at"] = used_at
            updated["request_count"] = int(row["request_count"]) + 1
        return _record(updated)

    def _resolve(self, selector: str) -> ApiKeyRecord:
        selector = str(selector or "").strip()
        if not selector:
            raise ValueError("请提供 API Key ID 或前缀。")
        records = self.list()
        exact = [
            record
            for record in records
            if selector in {record.id, record.prefix}
        ]
        if len(exact) == 1:
            return exact[0]
        partial = [
            record
            for record in records
            if record.id.startswith(selector) or record.prefix.startswith(selector)
        ]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise ValueError(f"API Key 选择器不唯一：{selector}")
        raise ValueError(f"未找到 API Key：{selector}")

    def _secret_hash(self, key_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT secret_hash FROM api_keys WHERE id = ?", (key_id,)
            ).fetchone()
        if row is None:
            raise ValueError("未找到 API Key。")
        return str(row["secret_hash"])

    def _available_name(self, connection: sqlite3.Connection, base: str) -> str:
        candidate = _validate_name(base)
        suffix = 1
        while connection.execute(
            "SELECT 1 FROM api_keys WHERE name = ? COLLATE NOCASE", (candidate,)
        ).fetchone():
            suffix += 1
            candidate = f"{base} {suffix}"
        return candidate

    def _initialize(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    prefix TEXT NOT NULL UNIQUE,
                    secret_hash TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    is_system INTEGER NOT NULL CHECK (is_system IN (0, 1)),
                    permissions_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    request_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(api_keys)")
            }
            if "permissions_json" not in columns:
                connection.execute(
                    "ALTER TABLE api_keys ADD COLUMN permissions_json TEXT"
                )
        self._secure_files()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _secure_files(self) -> None:
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.exists():
                os.chmod(path, 0o600)


def _record(row: sqlite3.Row | dict[str, object]) -> ApiKeyRecord:
    row_keys = row.keys()
    raw_permissions = (
        row["permissions_json"] if "permissions_json" in row_keys else None
    )
    return ApiKeyRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        prefix=str(row["prefix"]),
        enabled=bool(row["enabled"]),
        is_system=bool(row["is_system"]),
        permissions=_decode_permissions(raw_permissions),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_used_at=(
            str(row["last_used_at"]) if row["last_used_at"] is not None else None
        ),
        request_count=int(row["request_count"]),
    )


def _normalize_permissions(value: object) -> ApiKeyPermissions | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise ValueError("自定义权限至少需要选择一个模型。")
    normalized: ApiKeyPermissions = {}
    for raw_model, raw_efforts in value.items():
        model = str(raw_model or "").strip()
        if not model or len(model) > 120 or any(ord(char) < 32 for char in model):
            raise ValueError("模型名称无效。")
        if not isinstance(raw_efforts, (list, tuple, set)):
            raise ValueError(f"模型 {model} 的推理档位必须是列表。")
        efforts: list[str] = []
        for raw_effort in raw_efforts:
            effort = str(raw_effort or "").strip()
            if (
                not effort
                or len(effort) > 40
                or any(ord(char) < 32 for char in effort)
            ):
                raise ValueError(f"模型 {model} 包含无效的推理档位。")
            if effort not in efforts:
                efforts.append(effort)
        if not efforts:
            raise ValueError(f"模型 {model} 至少需要选择一个推理档位。")
        normalized[model] = tuple(efforts)
    return normalized


def _encode_permissions(value: ApiKeyPermissions | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode_permissions(value: object) -> ApiKeyPermissions | None:
    if value in {None, ""}:
        return None
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValueError("API Key 权限元数据已损坏。") from exc
    return _normalize_permissions(decoded)


def _validate_name(value: str) -> str:
    name = str(value or "").strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("API Key 名称长度必须在 1 到 80 个字符之间。")
    if any(ord(character) < 32 for character in name):
        raise ValueError("API Key 名称不能包含控制字符。")
    return name


def _validate_secret(value: str) -> str:
    secret = str(value or "").strip()
    if len(secret) < 24:
        raise ValueError("API Key 至少需要 24 个字符。")
    return secret


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit {result.returncode}").strip()


def _run_with_tty_input(
    command: list[str], value: str
) -> subprocess.CompletedProcess[str]:
    pid, master = pty.fork()
    if pid == 0:
        os.execv(command[0], command)

    output = bytearray()
    status: int | None = None
    prompts_answered = 0
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    output.extend(chunk)
                    lowered = bytes(output).lower()
                    prompt_count = lowered.count(b"password data for") + lowered.count(
                        b"retype password"
                    )
                    while prompts_answered < prompt_count:
                        os.write(master, (value + "\n").encode("utf-8"))
                        prompts_answered += 1
            waited, current_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status = current_status
                break
        if status is None:
            os.kill(pid, signal.SIGKILL)
            _, status = os.waitpid(pid, 0)
            detail = output.decode("utf-8", errors="replace").replace(
                value, "[redacted]"
            )
            return subprocess.CompletedProcess(
                command, 124, detail, "Keychain 写入超时"
            )
        return_code = os.waitstatus_to_exitcode(status)
        detail = output.decode("utf-8", errors="replace").replace(value, "[redacted]")
        return subprocess.CompletedProcess(
            command,
            return_code,
            detail,
            "",
        )
    finally:
        os.close(master)
