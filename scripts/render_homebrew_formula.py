from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "packaging" / "homebrew" / "csub.rb.template"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the csub Homebrew formula.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--arm64-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if VERSION_PATTERN.fullmatch(args.version) is None:
        parser.error("--version must be a semantic version")
    if SHA256_PATTERN.fullmatch(args.arm64_sha256) is None:
        parser.error("--arm64-sha256 must contain 64 lowercase hex characters")

    rendered = TEMPLATE.read_text(encoding="utf-8")
    rendered = rendered.replace("@VERSION@", args.version)
    rendered = rendered.replace("@ARM64_SHA256@", args.arm64_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
