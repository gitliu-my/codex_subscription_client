from __future__ import annotations

"""Minimal arrow-key menu using only the Python standard library."""

import os
import select
import sys
import termios
import tty
from contextlib import contextmanager
from typing import Callable, Iterator, Sequence, TextIO


KeyReader = Callable[[], str]


def select_option(
    title: str,
    options: Sequence[str],
    selected: int = 0,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    """Select one option with arrow keys and return its index."""

    if not options:
        raise ValueError("至少需要一个可选项。")
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    if not input_stream.isatty() or not output_stream.isatty():
        raise ValueError("交互选择需要在终端中运行；脚本中请传入命令行参数。")

    with _interactive_terminal(input_stream):
        output_stream.write("\x1b[?25l")
        output_stream.flush()
        try:
            return _selection_loop(
                title,
                options,
                selected,
                lambda: _read_key(input_stream.fileno()),
                output_stream,
            )
        finally:
            output_stream.write("\x1b[?25h")
            output_stream.flush()


def _selection_loop(
    title: str,
    options: Sequence[str],
    selected: int,
    read_key: KeyReader,
    output: TextIO,
) -> int:
    selected = max(0, min(selected, len(options) - 1))
    output.write(f"{title}\n")
    _draw_options(options, selected, output)

    while True:
        key = read_key()
        previous = selected
        if key in {"\x1b[A", "\x1bOA", "k"}:
            selected = (selected - 1) % len(options)
        elif key in {"\x1b[B", "\x1bOB", "j"}:
            selected = (selected + 1) % len(options)
        elif key in {"\x1b[H", "\x1bOH"}:
            selected = 0
        elif key in {"\x1b[F", "\x1bOF"}:
            selected = len(options) - 1
        elif key in {"\r", "\n"}:
            output.write("\n")
            output.flush()
            return selected
        elif key in {"\x03", "\x1b", "q"}:
            output.write("\n")
            output.flush()
            raise KeyboardInterrupt

        if selected != previous:
            output.write(f"\x1b[{len(options)}A")
            _draw_options(options, selected, output)


def _draw_options(options: Sequence[str], selected: int, output: TextIO) -> None:
    for index, label in enumerate(options):
        output.write("\r\x1b[2K")
        if index == selected:
            output.write(f"\x1b[1;36m> {label}\x1b[0m\n")
        else:
            output.write(f"  {label}\n")
    output.flush()


def _read_key(file_descriptor: int) -> str:
    first = os.read(file_descriptor, 1)
    if first != b"\x1b":
        return first.decode("utf-8", errors="ignore")

    sequence = bytearray(first)
    while len(sequence) < 3 and select.select([file_descriptor], [], [], 0.04)[0]:
        sequence.extend(os.read(file_descriptor, 1))
    return sequence.decode("ascii", errors="ignore")


@contextmanager
def _interactive_terminal(stream: TextIO) -> Iterator[None]:
    file_descriptor = stream.fileno()
    original = termios.tcgetattr(file_descriptor)
    try:
        tty.setcbreak(file_descriptor)
        yield
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, original)
