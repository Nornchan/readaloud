"""Extraction: a Source becomes a Document.

The dispatch lives here; the format-specific work lives in `html.py` and
`pdf.py`. A URL that turns out to serve a PDF is routed to the PDF extractor,
because plenty of "articles" are actually a link to a PDF.
"""

from __future__ import annotations

from pathlib import Path

from readaloud.document import Document
from readaloud.errors import ExtractionError, SourceError
from readaloud.extract.html import extract_html
from readaloud.extract.pdf import extract_pdf
from readaloud.fetch import fetch
from readaloud.source import Source, SourceKind
from readaloud.terminal import Reporter

__all__ = ["extract", "extract_html", "extract_pdf"]


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SourceError(
            f"could not read {path}: {exc.strerror or exc}",
            "Check the path and its permissions.",
        ) from exc


def extract(
    source: Source,
    use_cache: bool = True,
    reporter: Reporter | None = None,
) -> Document:
    """Fetch if needed, then extract. Raises ReadaloudError subclasses."""
    reporter = reporter or Reporter()

    if source.kind is SourceKind.URL:
        assert source.url is not None
        reporter.stage("fetch", source.url)
        response = fetch(source.url, use_cache=use_cache)
        reporter.stage(
            "fetch",
            f"{len(response.body):,} bytes"
            + (" (cached)" if response.from_cache else f" HTTP {response.status}"),
        )
        if response.is_pdf:
            reporter.stage("extract", "pdf")
            document = extract_pdf(response.body, path_label=source.url)
        else:
            reporter.stage("extract", "html")
            document = extract_html(response.text(), url=response.final_url)
        document.url = document.url or source.url

    elif source.kind is SourceKind.PDF:
        assert source.path is not None
        reporter.stage("extract", f"pdf {source.path}")
        document = extract_pdf(_read(source.path), path_label=str(source.path))
        document.path = source.path

    elif source.kind is SourceKind.HTML:
        assert source.path is not None
        reporter.stage("extract", f"html {source.path}")
        document = extract_html(_decode(_read(source.path)), url=None)
        document.path = source.path

    else:  # pragma: no cover - SourceKind is exhaustive
        raise ExtractionError(f"cannot extract from {source.kind}")

    if not document.title and source.path is not None:
        document.title = source.path.stem

    reporter.stage(
        "extract",
        f"{len(document.blocks)} blocks, {document.word_count():,} words"
        + (f", {document.page_count} pages" if document.page_count else ""),
    )
    return document
