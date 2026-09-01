"""Error types.

Every failure mode the user can hit gets its own class, its own exit code, and
its own hint saying what to do about it. A gated page, a scanned PDF and a
missing model file must not look like the same error.

Exit code 9 is retired: it belonged to the missing-API-key path, which went
away when the hosted engine did. Codes are not renumbered — scripts may key
off them.
"""

from __future__ import annotations


class ReadaloudError(Exception):
    """Base class for every expected, reportable failure.

    `message` says what went wrong; `hint` says what the user can do about it.
    Anything that escapes as a bare Exception is a bug, not a user error, and
    is reported differently.
    """

    exit_code = 1

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(ReadaloudError):
    """Bad or contradictory command-line arguments."""

    exit_code = 2


class SourceError(ReadaloudError):
    """The source argument isn't a URL, an HTML file, or a PDF we can read."""

    exit_code = 3


class FetchError(ReadaloudError):
    """The URL could not be retrieved."""

    exit_code = 4


class PaywallError(FetchError):
    """The page was retrieved but the article body is behind a paywall."""

    exit_code = 5


class ExtractionError(ReadaloudError):
    """The document was read but no usable article text came out of it."""

    exit_code = 6


class NoTextLayerError(ExtractionError):
    """A PDF with no text layer — a scan or an image-only export."""

    exit_code = 7


class ConfigError(ReadaloudError):
    """The config file is missing, unreadable, or malformed."""

    exit_code = 8


class SynthesisError(ReadaloudError):
    """The TTS backend failed."""

    exit_code = 10


class DependencyError(ReadaloudError):
    """An external program we shell out to (ffmpeg) is missing."""

    exit_code = 11
