from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ReleaseToolingTests(unittest.TestCase):
    def test_linux_release_scripts_are_executable_and_valid_shell(self) -> None:
        for name in (
            "build_linux.sh",
            "package_linux_release.sh",
            "install_linux.sh",
        ):
            path = ROOT / "scripts" / name
            self.assertTrue(os.access(path, os.X_OK), name)
            subprocess.run(["sh", "-n", str(path)], check=True)

    def test_homebrew_formula_renderer_replaces_release_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "Formula" / "csub.rb"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "render_homebrew_formula.py"),
                    "--version",
                    "1.2.3",
                    "--arm64-sha256",
                    "a" * 64,
                    "--output",
                    str(output),
                ],
                check=True,
            )
            formula = output.read_text(encoding="utf-8")

        self.assertIn("/download/v1.2.3/", formula)
        self.assertIn('version "1.2.3"', formula)
        self.assertIn(f'sha256 "{"a" * 64}"', formula)
        self.assertNotIn("@VERSION@", formula)
        self.assertNotIn("@ARM64_SHA256@", formula)


if __name__ == "__main__":
    unittest.main()
