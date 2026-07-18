from __future__ import annotations

"""Entry point used by the windowed macOS application bundle."""

from .ui import launch_dashboard


def main() -> int:
    launch_dashboard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
