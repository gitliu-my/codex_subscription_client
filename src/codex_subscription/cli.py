from __future__ import annotations

"""Command-line interface for browser login and connectivity checks."""

import argparse
import sys

from .auth import CodexOAuth, CodexOAuthError
from .client import CodexBackendError, CodexSubscriptionClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Use Codex models through ChatGPT subscription OAuth.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Open browser and sign in with ChatGPT.")
    login_parser.add_argument("--no-browser", action="store_true", help="Print URL without opening it.")
    login_parser.add_argument("--timeout", type=int, default=600, help="OAuth callback timeout in seconds.")

    subparsers.add_parser("status", help="Show local login status without printing tokens.")
    subparsers.add_parser("logout", help="Delete tokens stored by this module.")

    ask_parser = subparsers.add_parser("ask", help="Send one prompt to a subscription model.")
    ask_parser.add_argument("prompt", help="Prompt text.")
    ask_parser.add_argument("--model", default=None, help="Model name, e.g. gpt-5.6-luna.")
    ask_parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="Reasoning effort, e.g. low, medium, high, xhigh, or max.",
    )
    ask_parser.add_argument("--no-login", action="store_true", help="Fail instead of opening browser.")
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

        if args.command == "ask":
            client = CodexSubscriptionClient(
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                allow_login=not args.no_login,
                auth=auth,
            )
            print(client.generate(args.prompt))
            return 0
    except (CodexOAuthError, CodexBackendError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
