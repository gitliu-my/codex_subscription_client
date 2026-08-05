from __future__ import annotations

"""Command-line interface for browser login and connectivity checks."""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

from .api_keys import ApiKeyStore
from .auth import CodexOAuth, CodexOAuthError
from .client import (
    CodexBackendError,
    CodexSubscriptionClient,
    SubscriptionModel,
)
from .server import serve
from .service import (
    probe_api,
    restart_api_service,
    start_api_service,
    stop_api_service,
)
from .settings import DEFAULT_MAX_CONCURRENCY, REASONING_EFFORTS, SettingsStore
from .terminal_menu import select_option
from .ui import launch_dashboard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="csub",
        description="Use Codex models through ChatGPT subscription OAuth."
    )
    subparsers = parser.add_subparsers(dest="command")

    login_parser = subparsers.add_parser(
        "login", help="Open browser and sign in with ChatGPT."
    )
    login_parser.add_argument(
        "--no-browser", action="store_true", help="Print URL without opening it."
    )
    login_parser.add_argument(
        "--timeout", type=int, default=600, help="OAuth callback timeout in seconds."
    )

    subparsers.add_parser(
        "status", help="Show local login status without printing tokens."
    )
    subparsers.add_parser("logout", help="Delete tokens stored by this module.")
    subparsers.add_parser("start", help="Start the shared API in the background.")
    subparsers.add_parser("stop", help="Stop the shared background API.")
    subparsers.add_parser("restart", help="Restart the shared background API.")
    models_parser = subparsers.add_parser(
        "models", help="List models exposed to this subscription and client profile."
    )
    models_parser.add_argument(
        "--no-login", action="store_true", help="Fail instead of opening browser."
    )

    config_parser = subparsers.add_parser(
        "config", help="Choose and save the default model and reasoning effort."
    )
    config_parser.add_argument(
        "--model", default=None, help="Save this model without opening its menu."
    )
    config_parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="Save this effort without opening its menu.",
    )
    config_parser.add_argument(
        "--no-login", action="store_true", help="Fail instead of opening browser."
    )

    keys_parser = subparsers.add_parser(
        "keys", help="Create and manage application API keys."
    )
    key_subparsers = keys_parser.add_subparsers(dest="key_command")
    key_create = key_subparsers.add_parser("create", help="Create an API key.")
    key_create.add_argument("name", help="Application name for this key.")
    key_create_policy = key_create.add_mutually_exclusive_group()
    key_create_policy.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="MODEL=EFFORTS",
        help="Allow a model and comma-separated efforts; may be repeated.",
    )
    key_create_policy.add_argument(
        "--unrestricted",
        action="store_true",
        help="Allow every model and reasoning effort.",
    )
    key_reveal = key_subparsers.add_parser("reveal", help="Reveal an API key.")
    key_reveal.add_argument("key", help="Key ID or prefix.")
    key_rename = key_subparsers.add_parser("rename", help="Rename an API key.")
    key_rename.add_argument("key", help="Key ID or prefix.")
    key_rename.add_argument("name", help="New application name.")
    key_permissions = key_subparsers.add_parser(
        "permissions", help="Replace model and reasoning permissions."
    )
    key_permissions.add_argument("key", help="Key ID or prefix.")
    key_permission_policy = key_permissions.add_mutually_exclusive_group(
        required=True
    )
    key_permission_policy.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="MODEL=EFFORTS",
        help="Allow a model and comma-separated efforts; may be repeated.",
    )
    key_permission_policy.add_argument(
        "--unrestricted",
        action="store_true",
        help="Allow every model and reasoning effort.",
    )
    for command, help_text in (
        ("enable", "Enable an API key."),
        ("disable", "Disable an API key."),
    ):
        key_parser = key_subparsers.add_parser(command, help=help_text)
        key_parser.add_argument("key", help="Key ID or prefix.")
    key_delete = key_subparsers.add_parser("delete", help="Delete an API key.")
    key_delete.add_argument("key", help="Key ID or prefix.")
    key_delete.add_argument(
        "--yes", action="store_true", help="Confirm permanent deletion."
    )

    ask_parser = subparsers.add_parser("ask", help="Send one prompt to a subscription model.")
    ask_parser.add_argument("prompt", help="Prompt text.")
    ask_parser.add_argument("--model", default=None, help="Model name, e.g. gpt-5.6-luna.")
    ask_parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="Reasoning effort, e.g. low, medium, high, xhigh, or max.",
    )
    ask_parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="Local image path or HTTP/data URL. May be repeated.",
    )
    ask_parser.add_argument(
        "--no-login", action="store_true", help="Fail instead of opening browser."
    )
    ask_parser.add_argument(
        "--show-meta",
        action="store_true",
        help="Print requested model and backend response metadata.",
    )

    serve_parser = subparsers.add_parser(
        "serve", help="Run a local OpenAI-compatible API server."
    )
    serve_parser.add_argument(
        "--host", default=None, help="Bind host. Defaults to saved configuration."
    )
    serve_parser.add_argument(
        "--port", type=int, default=None, help="Bind port. Defaults to saved configuration."
    )
    serve_parser.add_argument(
        "--api-key",
        default=os.environ.get("CODEX_SUBSCRIPTION_API_KEY"),
        help="Bearer token required by the local API. Defaults to saved configuration.",
    )
    serve_parser.add_argument("--model", default=None, help="Default model.")
    serve_parser.add_argument(
        "--reasoning-effort", default=None, help="Default effort."
    )
    serve_parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help="Maximum simultaneous upstream requests. Defaults to saved configuration.",
    )
    serve_parser.add_argument("--no-login", action="store_true", help="Disable browser login.")
    ui_parser = subparsers.add_parser(
        "ui", help="Open the local browser dashboard."
    )
    ui_parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind host.")
    ui_parser.add_argument("--port", type=int, default=8320, help="Dashboard port.")
    ui_parser.add_argument(
        "--no-browser", action="store_true", help="Do not open the dashboard automatically."
    )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    auth = CodexOAuth()
    try:
        if args.command == "login":
            auth.login(timeout_seconds=args.timeout, open_browser=not args.no_browser)
            print(f"登录成功，token 已保存到：{auth.store.path}")
            return 0

        if args.command == "status":
            status = auth.status()
            config = SettingsStore().load()
            print(f"logged_in: {str(status.logged_in).lower()}")
            print(f"expired: {str(status.expired).lower()}")
            print(f"token_file: {status.token_path}")
            if status.account_id:
                print(f"chatgpt_account_id: {status.account_id}")
            print(f"default_model: {config['model']}")
            print(f"default_reasoning_effort: {config['reasoning_effort']}")
            print(f"max_concurrency: {config['max_concurrency']}")
            api_status = probe_api(config)
            print(f"api_status: {api_status.state}")
            if api_status.pid is not None:
                print(f"api_pid: {api_status.pid}")
            print(f"api_url: http://{config['host']}:{config['port']}/v1")
            return 0 if status.logged_in else 1

        if args.command == "logout":
            auth.logout()
            print("本模块保存的 Codex OAuth token 已删除。")
            return 0

        if args.command == "start":
            if not auth.status().logged_in:
                raise ValueError("尚未登录，请先运行 csub login。")
            config = SettingsStore().load_or_create()
            started, status = start_api_service(config)
            message = "后台 API 已启动" if started else "后台 API 已在运行"
            print(f"{message}：http://{config['host']}:{config['port']}/v1")
            if status.pid is not None:
                print(f"pid: {status.pid}")
            return 0

        if args.command == "stop":
            config = SettingsStore().load_or_create()
            stopped, _ = stop_api_service(config)
            print("后台 API 已停止。" if stopped else "后台 API 未运行。")
            return 0

        if args.command == "restart":
            if not auth.status().logged_in:
                raise ValueError("尚未登录，请先运行 csub login。")
            config = SettingsStore().load_or_create()
            status = restart_api_service(config)
            print(f"后台 API 已重启：http://{config['host']}:{config['port']}/v1")
            if status.pid is not None:
                print(f"pid: {status.pid}")
            return 0

        if args.command == "models":
            client = CodexSubscriptionClient(allow_login=not args.no_login, auth=auth)
            for model in client.list_models():
                reasoning = ",".join(model.supported_reasoning_efforts) or "-"
                modalities = ",".join(model.input_modalities) or "text"
                print(
                    f"{model.slug}\tdefault={model.default_reasoning_effort or '-'}"
                    f"\treasoning={reasoning}\tinput={modalities}"
                )
            return 0

        if args.command == "keys":
            config = SettingsStore().load_or_create()
            keys = ApiKeyStore()
            keys.ensure_legacy_key(config["api_key"])
            if args.key_command is None:
                print("STATUS\tTYPE\tPREFIX\tNAME\tPERMISSIONS\tREQUESTS\tLAST_USED")
                for record in keys.list():
                    print(
                        f"{'enabled' if record.enabled else 'disabled'}\t"
                        f"{'default' if record.is_system else 'app'}\t"
                        f"{record.prefix}\t{record.name}\t"
                        f"{_format_key_permissions(record.permissions)}\t"
                        f"{record.request_count}\t"
                        f"{record.last_used_at or '-'}"
                    )
                return 0
            if args.key_command == "create":
                if args.unrestricted:
                    permissions = None
                elif args.allow:
                    permissions = _parse_key_permissions(args.allow)
                else:
                    permissions = {
                        config["model"]: [config["reasoning_effort"]]
                    }
                record, secret = keys.create(args.name, permissions)
                print(f"API Key 已创建：{record.name}")
                print(f"id: {record.id}")
                print(f"key: {secret}")
                print(f"permissions: {_format_key_permissions(record.permissions)}")
                return 0
            if args.key_command == "reveal":
                print(keys.reveal(args.key))
                return 0
            if args.key_command == "rename":
                record = keys.rename(args.key, args.name)
                print(f"API Key 已重命名：{record.name}")
                return 0
            if args.key_command == "permissions":
                permissions = (
                    None
                    if args.unrestricted
                    else _parse_key_permissions(args.allow)
                )
                record = keys.set_permissions(args.key, permissions)
                print(
                    f"API Key 权限已更新：{record.name} -> "
                    f"{_format_key_permissions(record.permissions)}"
                )
                return 0
            if args.key_command in {"enable", "disable"}:
                enabled = args.key_command == "enable"
                record = keys.set_enabled(args.key, enabled)
                print(
                    f"API Key 已{'启用' if record.enabled else '禁用'}："
                    f"{record.name} ({record.prefix})"
                )
                return 0
            if args.key_command == "delete":
                if not args.yes:
                    raise ValueError("删除 API Key 需要显式传入 --yes。")
                record = keys.get(args.key)
                keys.delete(record.id)
                print(f"API Key 已删除：{record.name}")
                return 0

        if args.command == "config":
            store = SettingsStore()
            config = store.load_or_create()
            client = CodexSubscriptionClient(
                model=config["model"],
                reasoning_effort=config["reasoning_effort"],
                allow_login=not args.no_login,
                auth=auth,
            )
            try:
                print("正在获取当前订阅可用模型...", flush=True)
                model, effort = _choose_configuration(
                    client.list_models(),
                    config,
                    requested_model=args.model,
                    requested_effort=args.reasoning_effort,
                )
            except KeyboardInterrupt:
                print("\n已取消配置。")
                return 130
            store.update(model=model.slug, reasoning_effort=effort)
            print("\n默认配置已保存：")
            print(f"model: {model.slug}")
            print(f"reasoning_effort: {effort}")
            print(f"settings_file: {store.path}")
            return 0

        if args.command == "ask":
            config = SettingsStore().load()
            model, effort = _resolve_client_defaults(
                config, args.model, args.reasoning_effort
            )
            client = CodexSubscriptionClient(
                model=model,
                reasoning_effort=effort,
                allow_login=not args.no_login,
                auth=auth,
            )
            response = client.generate_response(args.prompt, images=args.image)
            print(response.require_text())
            if args.show_meta:
                print("\n--- metadata ---")
                print(f"requested_model: {client.model}")
                print(f"response_model: {response.model or 'not_returned'}")
                print(f"reasoning_effort: {client.reasoning_effort}")
                print(f"response_id: {response.response_id or 'not_returned'}")
                if response.usage:
                    print(f"input_tokens: {response.usage.get('input_tokens', 'not_returned')}")
                    print(f"output_tokens: {response.usage.get('output_tokens', 'not_returned')}")
                    print(f"total_tokens: {response.usage.get('total_tokens', 'not_returned')}")
            return 0

        if args.command == "serve":
            config = SettingsStore().load_or_create()
            model, effort = _resolve_client_defaults(
                config, args.model, args.reasoning_effort
            )
            client = CodexSubscriptionClient(
                model=model,
                reasoning_effort=effort,
                allow_login=not args.no_login,
                auth=auth,
            )
            try:
                serve(
                    args.host or config["host"],
                    args.port or config["port"],
                    args.api_key or config["api_key"],
                    client,
                    show_api_key=os.environ.get("CSUB_BACKGROUND") != "1",
                    max_concurrency=(
                        args.max_concurrency
                        if args.max_concurrency is not None
                        else int(
                            config.get(
                                "max_concurrency", DEFAULT_MAX_CONCURRENCY
                            )
                        )
                    ),
                )
            except KeyboardInterrupt:
                print("\n本地 API 已停止。")
            return 0

        if args.command == "ui":
            try:
                launch_dashboard(args.host, args.port, open_browser=not args.no_browser)
            except KeyboardInterrupt:
                print("\n管理界面已停止。")
            return 0
    except (CodexOAuthError, CodexBackendError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2


MenuSelector = Callable[[str, Sequence[str], int], int]


def _parse_key_permissions(values: Sequence[str]) -> dict[str, list[str]]:
    permissions: dict[str, list[str]] = {}
    for value in values:
        model, separator, effort_text = value.partition("=")
        model = model.strip()
        efforts = [effort.strip() for effort in effort_text.split(",") if effort.strip()]
        if not separator or not model or not efforts:
            raise ValueError(
                "权限格式必须是 MODEL=EFFORT[,EFFORT]，例如 "
                "gpt-5.6-luna=low,medium。"
            )
        bucket = permissions.setdefault(model, [])
        for effort in efforts:
            if effort not in bucket:
                bucket.append(effort)
    return permissions


def _format_key_permissions(
    permissions: dict[str, tuple[str, ...]] | None,
) -> str:
    if permissions is None:
        return "all"
    return ";".join(
        f"{model}={','.join(efforts)}"
        for model, efforts in permissions.items()
    )


def _choose_configuration(
    models: Sequence[SubscriptionModel],
    config: dict[str, Any],
    requested_model: str | None = None,
    requested_effort: str | None = None,
    selector: MenuSelector = select_option,
) -> tuple[SubscriptionModel, str]:
    if not models:
        raise ValueError("当前订阅没有返回可选模型。")

    model_by_slug = {model.slug: model for model in models}
    if requested_model:
        model = model_by_slug.get(requested_model)
        if model is None:
            raise ValueError(f"当前订阅未返回模型：{requested_model}")
    else:
        model_slugs = [model.slug for model in models]
        selected = _preferred_index(model_slugs, str(config.get("model") or ""))
        labels = [_model_label(model) for model in models]
        model = models[
            selector("选择默认模型（方向键移动，回车确认）", labels, selected)
        ]

    efforts = list(model.supported_reasoning_efforts)
    if not efforts:
        efforts = list(REASONING_EFFORTS)
    if requested_effort:
        if requested_effort not in efforts:
            raise ValueError(
                f"模型 {model.slug} 不支持推理档位：{requested_effort}；"
                f"可选：{', '.join(efforts)}"
            )
        effort = requested_effort
    else:
        preferred = str(config.get("reasoning_effort") or "")
        if preferred not in efforts:
            preferred = model.default_reasoning_effort or efforts[0]
        selected = _preferred_index(efforts, preferred)
        labels = [
            f"{effort}{'（模型默认）' if effort == model.default_reasoning_effort else ''}"
            for effort in efforts
        ]
        effort = efforts[
            selector("选择默认推理档位（方向键移动，回车确认）", labels, selected)
        ]
    return model, effort


def _resolve_client_defaults(
    config: dict[str, Any],
    requested_model: str | None,
    requested_effort: str | None,
) -> tuple[str, str]:
    model = (
        requested_model
        or os.environ.get("CODEX_SUBSCRIPTION_MODEL")
        or str(config["model"])
    )
    effort = (
        requested_effort
        or os.environ.get("CODEX_SUBSCRIPTION_REASONING_EFFORT")
        or str(config["reasoning_effort"])
    )
    return model, effort


def _preferred_index(options: Sequence[str], preferred: str) -> int:
    try:
        return options.index(preferred)
    except ValueError:
        return 0


def _model_label(model: SubscriptionModel) -> str:
    modalities = ",".join(model.input_modalities) or "text"
    if model.display_name and model.display_name != model.slug:
        return f"{model.display_name}  [{model.slug}]  input={modalities}"
    return f"{model.slug}  input={modalities}"


if __name__ == "__main__":
    raise SystemExit(main())
