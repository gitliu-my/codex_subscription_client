from __future__ import annotations

"""Command-line interface for browser login and connectivity checks."""

import argparse
import sys

from .auth import CodexOAuth, CodexOAuthError
from .client import CodexBackendError, CodexSubscriptionClient
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Use Codex models through ChatGPT subscription OAuth."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    models_parser = subparsers.add_parser(
        "models", help="List models exposed to this subscription and client profile."
    )
    models_parser.add_argument(
        "--no-login", action="store_true", help="Fail instead of opening browser."
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

    serve_parser = subparsers.add_parser(
        "serve", help="Run a local OpenAI-compatible API server."
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    serve_parser.add_argument("--port", type=int, default=8317, help="Bind port.")
    serve_parser.add_argument(
        "--api-key",
        default=None,
        help="Optional bearer token required by the local API.",
    )
    serve_parser.add_argument("--model", default=None, help="Default model.")
    serve_parser.add_argument(
        "--reasoning-effort", default=None, help="Default effort."
    )
    serve_parser.add_argument("--no-login", action="store_true", help="Disable browser login.")
    args = parser.parse_args(argv)

    auth = CodexOAuth()
    try:
        if args.command == "login":
            auth.login(timeout_seconds=args.timeout, open_browser=not args.no_browser)
            print(f"登录成功，token 已保存到：{auth.store.path}")
            return 0

        if args.command == "status":
            status = auth.status()
            print(f"logged_in: {str(status.logged_in).lower()}")
            print(f"expired: {str(status.expired).lower()}")
            print(f"token_file: {status.token_path}")
            if status.account_id:
                print(f"chatgpt_account_id: {status.account_id}")
            return 0 if status.logged_in else 1

        if args.command == "logout":
            auth.logout()
            print("本模块保存的 Codex OAuth token 已删除。")
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

        if args.command == "ask":
            client = CodexSubscriptionClient(
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                allow_login=not args.no_login,
                auth=auth,
            )
            print(client.generate(args.prompt, images=args.image))
            return 0

        if args.command == "serve":
            client = CodexSubscriptionClient(
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                allow_login=not args.no_login,
                auth=auth,
            )
            try:
                serve(args.host, args.port, args.api_key, client)
            except KeyboardInterrupt:
                print("\n本地 API 已停止。")
            return 0
    except (CodexOAuthError, CodexBackendError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
