"""Working out where the output goes."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from readaloud.document import Document

MAX_STEM = 80
FALLBACK_STEM = "readaloud"


def slugify(text: str) -> str:
    """A filename-safe, ASCII, hyphenated stem."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_only).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    if len(slug) > MAX_STEM:
        slug = slug[:MAX_STEM].rsplit("-", 1)[0] or slug[:MAX_STEM]
    return slug


def default_stem(document: Document) -> str:
    """Name the file after the article, falling back to the source."""
    if document.title:
        slug = slugify(document.title)
        if slug:
            return slug
    if document.path:
        slug = slugify(document.path.stem)
        if slug:
            return slug
    if document.url:
        parsed = urlparse(document.url)
        tail = Path(parsed.path).stem or parsed.netloc
        slug = slugify(tail)
        if slug:
            return slug
    return FALLBACK_STEM


def _unused(path: Path) -> Path:
    """Add -2, -3 … rather than silently overwriting a derived name."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for index in range(2, 1000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    return path


def audio_path(document: Document, fmt: str, requested: Path | None) -> Path:
    """Where the audio goes.

    An explicit --out is taken literally and overwrites, which is what a flag
    should do. A derived name never clobbers an existing file.
    """
    if requested is not None:
        path = requested.expanduser()
        if path.is_dir():
            return path / f"{default_stem(document)}.{fmt}"
        if not path.suffix:
            return path.with_suffix(f".{fmt}")
        return path
    return _unused(Path.cwd() / f"{default_stem(document)}.{fmt}")


def text_path(audio: Path) -> Path:
    """`--keep-text` writes next to the audio, same stem."""
    return audio.with_suffix(".txt")
