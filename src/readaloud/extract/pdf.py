"""PDF → structured Document, via PyMuPDF.

Two things here are load-bearing:

*Reading order.* A two-column paper read naively comes out interleaved, one
line of column A followed by one line of column B, which is unlistenable. We
find the vertical gutter between columns and read left column then right,
banded by any full-width blocks (title, abstract, figures) so a paper that
switches between one and two columns still reads correctly.

*Line breaks are preserved.* Block text keeps the newlines the PDF actually
had. Undoing hard wrapping and line-break hyphenation is the normalizer's job
and it needs to see where the lines broke.
"""

from __future__ import annotations

import re

import pymupdf

from readaloud.document import Block, BlockKind, Document
from readaloud.errors import ExtractionError, NoTextLayerError

# Characters per page below which we call it a scan rather than a document.
MIN_CHARS_PER_PAGE = 8
MIN_CHARS_TOTAL = 50

# Gutter search: only look in the middle of the page, and only accept a gap
# at least this wide relative to page width.
_GUTTER_BAND = (0.30, 0.70)
_MIN_GUTTER_RATIO = 0.015
_BINS = 240

_CAPTION = re.compile(
    r"^\s*(figure|fig\.?|table|chart|plate|exhibit|scheme)\s*\d+[.:)]?\s",
    re.IGNORECASE,
)

_BOLD_FLAG = 1 << 4


class _RawBlock:
    """A text block with the geometry and font data we need to classify it."""

    __slots__ = ("text", "x0", "y0", "x1", "y1", "size", "chars")

    def __init__(self, text: str, bbox: tuple[float, float, float, float],
                 size: float) -> None:
        self.text = text
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.size = size
        self.chars = len(text)


def _page_blocks(page: pymupdf.Page) -> list[_RawBlock]:
    """Text blocks on one page, with their dominant font size."""
    raw = page.get_text("dict")
    blocks: list[_RawBlock] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue  # an image
        lines: list[str] = []
        sizes: list[tuple[float, int]] = []
        for line in block.get("lines", []):
            pieces = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                pieces.append(text)
                sizes.append((round(span.get("size", 0.0), 1), len(text)))
            joined = "".join(pieces).strip()
            if joined:
                lines.append(joined)
        text = "\n".join(lines)
        if not text.strip():
            continue
        # The size that covers the most characters, not the largest size — a
        # single large drop-cap should not make a paragraph look like a heading.
        dominant = max(sizes, key=lambda pair: pair[1])[0] if sizes else 0.0
        blocks.append(_RawBlock(text, tuple(block["bbox"]), dominant))
    return blocks


def _find_gutter(blocks: list[_RawBlock], width: float) -> float | None:
    """The x of a vertical gap splitting the page into two columns, if there is one."""
    if len(blocks) < 4 or width <= 0:
        return None

    # Full-width elements — the title, the authors, a wide figure — cover the
    # gutter, so projecting *every* block hides it and the page silently falls
    # back to a naive top-to-bottom sort. Excluding them by width is not
    # enough: a centred second line of a title is narrow but still straddles
    # the gap. The test that works is whether a block crosses the page's
    # midpoint, which no block in a column of a two-column layout does.
    #
    # It also gives the right answer for free on a single-column page, where
    # nearly every block crosses the middle and too few survive to look at.
    centre = width / 2
    column_blocks = [block for block in blocks if not block.x0 < centre < block.x1]
    if len(column_blocks) < 4:
        return None

    occupied = [False] * _BINS
    scale = _BINS / width
    for block in column_blocks:
        start = max(0, int(block.x0 * scale))
        end = min(_BINS - 1, int(block.x1 * scale))
        for index in range(start, end + 1):
            occupied[index] = True

    low, high = int(_BINS * _GUTTER_BAND[0]), int(_BINS * _GUTTER_BAND[1])
    best: tuple[int, int] | None = None
    run_start: int | None = None
    for index in range(low, high + 1):
        if not occupied[index]:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            if best is None or index - run_start > best[1] - best[0]:
                best = (run_start, index)
            run_start = None
    if run_start is not None and (best is None or high + 1 - run_start > best[1] - best[0]):
        best = (run_start, high + 1)

    if best is None:
        return None
    gap_bins = best[1] - best[0]
    if gap_bins / _BINS < _MIN_GUTTER_RATIO:
        return None

    split = (best[0] + best[1]) / 2 / scale
    left = [b for b in blocks if b.x1 <= split]
    right = [b for b in blocks if b.x0 >= split]
    # Both columns must carry real content, or this is just a wide margin.
    if len(left) < 2 or len(right) < 2:
        return None
    left_chars = sum(b.chars for b in left)
    right_chars = sum(b.chars for b in right)
    total = left_chars + right_chars
    if total == 0 or min(left_chars, right_chars) / total < 0.2:
        return None
    return split


def _reading_order(blocks: list[_RawBlock], width: float) -> list[_RawBlock]:
    """Order blocks the way a person reads them."""
    if not blocks:
        return []
    split = _find_gutter(blocks, width)
    if split is None:
        return sorted(blocks, key=lambda b: (round(b.y0, 1), b.x0))

    ordered: list[_RawBlock] = []
    left: list[_RawBlock] = []
    right: list[_RawBlock] = []

    def flush() -> None:
        ordered.extend(sorted(left, key=lambda b: b.y0))
        ordered.extend(sorted(right, key=lambda b: b.y0))
        left.clear()
        right.clear()

    for block in sorted(blocks, key=lambda b: b.y0):
        spans_gutter = block.x0 < split and block.x1 > split
        if spans_gutter:
            # A full-width block (title, abstract, wide figure) ends the
            # two-column band above it and starts a new one below.
            flush()
            ordered.append(block)
        elif block.x1 <= split:
            left.append(block)
        else:
            right.append(block)
    flush()
    return ordered


def _body_size(blocks: list[_RawBlock]) -> float:
    """The font size that most of the document's text is set in."""
    weights: dict[float, int] = {}
    for block in blocks:
        weights[block.size] = weights.get(block.size, 0) + block.chars
    if not weights:
        return 0.0
    return max(weights.items(), key=lambda pair: pair[1])[0]


def _classify(block: _RawBlock, body_size: float) -> tuple[BlockKind, int]:
    text = block.text
    if _CAPTION.match(text):
        return BlockKind.CAPTION, 0
    words = len(text.split())
    if body_size and block.size >= body_size * 1.12 and words <= 25:
        ratio = block.size / body_size
        level = 1 if ratio >= 1.6 else (2 if ratio >= 1.3 else 3)
        return BlockKind.HEADING, level
    return BlockKind.PARAGRAPH, 0


# Two heading blocks this close together vertically are one heading that wrapped.
_HEADING_LINE_GAP = 0.05


def _merge_split_headings(blocks: list[Block]) -> list[Block]:
    """Rejoin a heading that wrapped onto a second line.

    A PDF title set over two lines arrives as two heading blocks, and the
    normalizer then punctuates each one — "…Transformers for." followed by
    "Language Understanding." — which is audibly wrong. Adjacency is what
    separates this from two consecutive section headings, so the vertical gap
    has to be tiny.
    """
    merged: list[Block] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.kind is BlockKind.HEADING
            and block.kind is BlockKind.HEADING
            and previous.level == block.level
            and previous.page == block.page
            and previous.position is not None
            and block.position is not None
            and 0 <= block.position - previous.position < _HEADING_LINE_GAP
            and not previous.text.rstrip().endswith((".", "!", "?", ":"))
        ):
            previous.text = f"{previous.text.rstrip()} {block.text.lstrip()}"
            continue
        merged.append(block)
    return merged


def _title_from_headings(blocks: list[Block]) -> str | None:
    """Fall back to the first heading near the top of page one.

    Better than the filename, which is what produced a spoken header reading
    simply "bert."
    """
    for block in blocks:
        if block.page not in (None, 1):
            break
        if (
            block.kind is BlockKind.HEADING
            and block.level <= 2
            and (block.position is None or block.position < 0.35)
        ):
            text = " ".join(block.text.split())
            if 3 <= len(text) <= 200:
                return text
    return None


def extract_pdf(data: bytes, path_label: str = "the PDF") -> Document:
    """Read a PDF into blocks. Raises when there is no text layer to read."""
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises a variety of types
        raise ExtractionError(
            f"could not open {path_label}: {exc}",
            "The file may be corrupt. Try re-downloading it.",
        ) from exc

    if document.needs_pass:
        document.close()
        raise ExtractionError(
            f"{path_label} is password-protected",
            "Remove the password (Preview: File → Export as PDF) and try again.",
        )

    blocks: list[Block] = []
    all_raw: list[_RawBlock] = []
    per_page: list[tuple[int, float, list[_RawBlock]]] = []

    for number, page in enumerate(document, start=1):
        rect = page.rect
        raw_blocks = _page_blocks(page)
        ordered = _reading_order(raw_blocks, rect.width)
        per_page.append((number, rect.height, ordered))
        all_raw.extend(ordered)

    total_chars = sum(block.chars for block in all_raw)
    page_count = document.page_count
    metadata = dict(document.metadata or {})
    document.close()

    floor = max(MIN_CHARS_TOTAL, MIN_CHARS_PER_PAGE * page_count)
    if total_chars < floor:
        raise NoTextLayerError(
            f"{path_label} has no text layer — only {total_chars} characters "
            f"across {page_count} page{'s' if page_count != 1 else ''}",
            "It is probably a scan. Run it through OCR first "
            "(`brew install ocrmypdf && ocrmypdf in.pdf out.pdf`) and try again.",
        )

    body_size = _body_size(all_raw)
    for number, height, ordered in per_page:
        for raw in ordered:
            kind, level = _classify(raw, body_size)
            blocks.append(
                Block(
                    kind=kind,
                    text=raw.text,
                    level=level,
                    page=number,
                    position=(raw.y0 / height) if height else None,
                )
            )

    blocks = _merge_split_headings(blocks)

    title = (metadata.get("title") or "").strip() or None
    author = (metadata.get("author") or "").strip() or None
    # Plenty of PDFs carry a producer's junk title like "Microsoft Word - doc1".
    if title and re.match(r"^(microsoft word|untitled|document\d*)\b", title, re.I):
        title = None
    if not title:
        title = _title_from_headings(blocks)

    return Document(
        blocks=blocks,
        title=title,
        author=author,
        date=(metadata.get("creationDate") or "").strip() or None,
        page_count=page_count,
    )
