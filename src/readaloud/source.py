"""Source-type detection.

The user passes one argument and we work out what it is — no `--type` flag.
Order of decisions, most certain first:

1. An explicit URL scheme (`http://`, `https://`) is a URL. `file://` is
   unwrapped to a local path.
2. Anything that names an existing file is sniffed by content (magic bytes),
   then by extension. Content wins, because `article.txt` that is really a PDF
   should still work and `report.pdf` that is really HTML should not blow up in
   the PDF parser.
3. A bare `example.com/article` with no matching local file is treated as an
   https URL. This is the shell-paste case and is worth the small ambiguity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from readaloud.errors import SourceError

# Enough of a domain to be plausible: label.label[.label]/optional path.
_BARE_DOMAIN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?::\d+)?(?:[/?#].*)?$",
    re.IGNORECASE,
)

_HTML_EXTENSIONS = {".html", ".htm", ".xhtml", ".xht"}
_PDF_EXTENSIONS = {".pdf"}

# How far into a file we look for a signature.
_SNIFF_BYTES = 2048

_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<?xml")


class SourceKind(Enum):
    URL = "url"
    HTML = "html"
    PDF = "pdf"


@dataclass(frozen=True)
class Source:
    """A resolved input: what it is and where it lives."""

    kind: SourceKind
    raw: str
    url: str | None = None
    path: Path | None = None

    @property
    def is_url(self) -> bool:
        return self.kind is SourceKind.URL

    def describe(self) -> str:
        target = self.url if self.is_url else str(self.path)
        return f"{self.kind.value} {target}"


def _sniff(path: Path) -> SourceKind | None:
    """Identify a file by its leading bytes. None when nothing matches."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError as exc:
        raise SourceError(
            f"could not read {path}: {exc.strerror or exc}",
            "Check the path and its permissions.",
        ) from exc

    if head.startswith(b"%PDF-"):
        return SourceKind.PDF

    lowered = head.lstrip().lower()
    if any(lowered.startswith(marker) for marker in _HTML_MARKERS):
        return SourceKind.HTML
    # Some pages open with a comment or a stray BOM before <html>.
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return SourceKind.HTML
    return None


def _from_path(path: Path, raw: str) -> Source:
    if path.is_dir():
        raise SourceError(
            f"{path} is a directory, not a document",
            "Pass a .html or .pdf file, or a URL.",
        )
    if not path.exists():
        raise SourceError(
            f"no such file: {path}",
            "Pass an existing .html or .pdf file, or a URL starting with https://.",
        )

    sniffed = _sniff(path)
    if sniffed is not None:
        return Source(kind=sniffed, raw=raw, path=path)

    suffix = path.suffix.lower()
    if suffix in _PDF_EXTENSIONS:
        raise SourceError(
            f"{path} has a .pdf extension but is not a PDF file",
            "The file may be truncated or corrupt — try re-downloading it.",
        )
    if suffix in _HTML_EXTENSIONS:
        # An HTML fragment with no <html> wrapper is still fine to extract from.
        return Source(kind=SourceKind.HTML, raw=raw, path=path)

    raise SourceError(
        f"unsupported file type: {path.name}",
        "readaloud reads .html and .pdf files, or URLs.",
    )


def detect(raw: str) -> Source:
    """Resolve a user-supplied source argument into a `Source`.

    Raises `SourceError` with an actionable hint when it can't.
    """
    candidate = raw.strip()
    if not candidate:
        raise SourceError(
            "empty source argument",
            "Pass a URL, an .html file, or a .pdf file.",
        )

    parsed = urlparse(candidate)

    if parsed.scheme in ("http", "https"):
        if not parsed.netloc:
            raise SourceError(
                f"{candidate} is not a complete URL",
                "URLs need a host, e.g. https://example.com/article.",
            )
        return Source(kind=SourceKind.URL, raw=raw, url=candidate)

    if parsed.scheme == "file":
        local = Path(url2pathname(unquote(parsed.path))).expanduser()
        return _from_path(local.resolve(), raw)

    # A Windows-style drive letter or an unknown scheme like `ftp:` — reject the
    # latter clearly rather than treating it as a filename.
    if parsed.scheme and len(parsed.scheme) > 1:
        raise SourceError(
            f"unsupported URL scheme: {parsed.scheme}://",
            "readaloud fetches http:// and https:// URLs only.",
        )

    path = Path(candidate).expanduser()
    if path.exists():
        return _from_path(path.resolve(), raw)

    if _BARE_DOMAIN.match(candidate):
        return Source(kind=SourceKind.URL, raw=raw, url=f"https://{candidate}")

    raise SourceError(
        f"could not tell what {candidate!r} is",
        "Pass a URL (https://…), an .html file, or a .pdf file.",
    )
