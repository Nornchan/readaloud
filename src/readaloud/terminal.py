"""Terminal output: colour, verbosity, and error reporting.

Colour is on only when stderr is a TTY and `NO_COLOR` is unset, per
https://no-color.org. Progress bars follow the same rule (see the synthesis
milestone) so piped output stays clean.
"""

from __future__ import annotations

import os
import sys
from typing import IO, TextIO

from readaloud.errors import ReadaloudError

_RESET = "\033[0m"
_STYLES = {
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "dim": "\033[2m",
    "bold": "\033[1m",
}


def color_enabled(stream: IO[str] | None = None) -> bool:
    """True when it's safe to emit ANSI escapes on `stream` (default stderr)."""
    stream = stream or sys.stderr
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def style(text: str, name: str, stream: IO[str] | None = None) -> str:
    """Wrap `text` in a style, or return it unchanged when colour is off."""
    if not color_enabled(stream) or name not in _STYLES:
        return text
    return f"{_STYLES[name]}{text}{_RESET}"


class Reporter:
    """Stage logging for --verbose, plus consistent error formatting.

    Everything here goes to stderr. stdout is reserved for the tool's actual
    output (the result path, --estimate figures, --list-voices), so
    `readaloud ... | pbcopy` stays useful.
    """

    def __init__(self, verbose: bool = False, stream: TextIO | None = None) -> None:
        self.verbose = verbose
        self.stream = stream or sys.stderr

    def stage(self, name: str, detail: str = "") -> None:
        """Announce a pipeline stage. Silent unless --verbose."""
        if not self.verbose:
            return
        label = style(f"[{name}]", "dim", self.stream)
        line = f"{label} {detail}" if detail else label
        print(line, file=self.stream)

    def note(self, message: str) -> None:
        """Progress the user always wants, verbose or not."""
        print(style(message, "dim", self.stream), file=self.stream)

    def progress(self, done: int, total: int, label: str = "") -> None:
        """A single rewriting line. Silent when piped — the spec forbids a
        progress bar on non-TTY output, but silence during a multi-minute
        synthesis is also unacceptable, so a non-TTY gets nothing here and
        the stage lines carry the load instead."""
        if not self.stream.isatty() or total <= 0:
            return
        width = 24
        filled = int(width * done / total)
        bar = "#" * filled + "." * (width - filled)
        print(f"\r  {label} [{bar}] {done}/{total}", end="", file=self.stream)

    def progress_done(self) -> None:
        if self.stream.isatty():
            print(file=self.stream)

    def warn(self, message: str) -> None:
        print(f"{style('warning:', 'yellow', self.stream)} {message}", file=self.stream)

    def error(self, message: str, hint: str | None = None) -> None:
        prefix = style("readaloud: error:", "red", self.stream)
        print(f"{prefix} {message}", file=self.stream)
        if hint:
            print(f"  {style('→', 'dim', self.stream)} {hint}", file=self.stream)

    def report(self, exc: ReadaloudError) -> int:
        """Print a ReadaloudError and return the exit code to use."""
        self.error(exc.message, exc.hint)
        return exc.exit_code
