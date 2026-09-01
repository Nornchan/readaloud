"""The structured document that extraction produces and normalization consumes.

Extraction deliberately does *not* flatten to a string. The normalizer needs to
know a heading from a paragraph from a pull quote, and the PDF header/footer
rules need to know which page a block came from and how far down it sat. Once
that structure is thrown away it cannot be recovered.

See SPEC.md "Appendix A — the intermediate representation" for the contract
these types implement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class BlockKind(Enum):
    """What a block *is*, independent of how it will be spoken."""

    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    CAPTION = "caption"
    TABLE = "table"
    CODE = "code"


@dataclass
class Block:
    """One unit of extracted content.

    `text` is raw: entities resolved and whitespace preserved as extracted.
    In particular PDF blocks keep their original line breaks, because undoing
    hard wrapping and line-break hyphenation is the normalizer's job and it
    needs to see where the lines actually broke.
    """

    kind: BlockKind
    text: str
    level: int = 0
    """Heading depth (1–6), or list nesting depth. 0 when not applicable."""

    ordinal: int | None = None
    """Position within an ordered list, 1-based. None for unordered items."""

    page: int | None = None
    """1-based page number. PDF only."""

    position: float | None = None
    """Vertical position of the block's top edge as a fraction of page height,
    0.0 at the top and 1.0 at the bottom. PDF only — this is what running
    header and footer detection keys off."""

    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class Document:
    """Everything extraction knows about one source."""

    blocks: list[Block] = field(default_factory=list)
    title: str | None = None
    author: str | None = None
    date: str | None = None
    url: str | None = None
    path: Path | None = None
    page_count: int | None = None

    def word_count(self) -> int:
        return sum(block.word_count() for block in self.blocks)

    def char_count(self) -> int:
        return sum(len(block.text) for block in self.blocks)

    def of_kind(self, *kinds: BlockKind) -> list[Block]:
        return [block for block in self.blocks if block.kind in kinds]

    def source_label(self) -> str:
        return self.url or (str(self.path) if self.path else "")

    def plain_text(self) -> str:
        """A readable rendering, for `--keep-text` and for eyeballing output.

        This is the extraction result, not the text that will be spoken — once
        the normalizer lands, `--keep-text` writes its output instead, so that
        what you read is what the engine was given.
        """
        lines: list[str] = []
        for block in self.blocks:
            text = re.sub(r"\s+", " ", block.text).strip()
            if not text:
                continue
            if block.kind is BlockKind.TITLE:
                lines += [text, "=" * len(text), ""]
            elif block.kind is BlockKind.HEADING:
                lines += ["", f"{'#' * max(1, block.level)} {text}", ""]
            elif block.kind is BlockKind.LIST_ITEM:
                marker = f"{block.ordinal}." if block.ordinal else "-"
                lines.append(f"{marker} {text}")
            elif block.kind is BlockKind.QUOTE:
                lines += [f"> {text}", ""]
            elif block.kind is BlockKind.CAPTION:
                lines += [f"[caption] {text}", ""]
            elif block.kind is BlockKind.TABLE:
                lines += [f"[table] {text}", ""]
            elif block.kind is BlockKind.CODE:
                lines += ["[code]", text, ""]
            else:
                lines += [text, ""]
        return "\n".join(lines).strip() + "\n"
