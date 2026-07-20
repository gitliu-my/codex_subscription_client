from __future__ import annotations

import io
import unittest

from codex_subscription.terminal_menu import _selection_loop, select_option


class TerminalMenuTests(unittest.TestCase):
    def test_arrow_keys_move_and_wrap(self) -> None:
        keys = iter(["\x1b[A", "\r"])
        output = io.StringIO()

        selected = _selection_loop(
            "Choose", ["first", "second", "third"], 0, lambda: next(keys), output
        )

        self.assertEqual(selected, 2)
        self.assertIn("> third", output.getvalue())

    def test_home_end_and_vim_keys_are_supported(self) -> None:
        keys = iter(["\x1b[F", "k", "j", "\r"])
        selected = _selection_loop(
            "Choose",
            ["first", "second", "third"],
            1,
            lambda: next(keys),
            io.StringIO(),
        )
        self.assertEqual(selected, 2)

    def test_cancel_raises_keyboard_interrupt(self) -> None:
        with self.assertRaises(KeyboardInterrupt):
            _selection_loop(
                "Choose", ["first"], 0, lambda: "q", io.StringIO()
            )

    def test_interactive_menu_rejects_non_tty_streams(self) -> None:
        with self.assertRaisesRegex(ValueError, "需要在终端"):
            select_option(
                "Choose",
                ["first"],
                input_stream=io.StringIO(),
                output_stream=io.StringIO(),
            )


if __name__ == "__main__":
    unittest.main()
