"""Normalization: a Document becomes speech.

Every rule in SPEC.md Appendix A.5 lives here as a named function, in the
order they run, because later rules assume earlier ones have happened. The
order is the contract; the thresholds are meant to be tuned.

Text that reads fine looks terrible out loud. This module is the difference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from readaloud.abbreviations import (
    EMAIL_PATTERN,
    EXPANSIONS,
    PROTECTED,
    SPELL_OUT,
    SPELL_TOKEN,
    URL_PATTERN,
)
from readaloud.document import Block, BlockKind, Document
from readaloud.speech import Pause, Pauses, Say, Speech, Spell

# --- tunables --------------------------------------------------------------

RUNNING_ARTIFACT_PAGES = 3
"""A short block repeating on this many pages is a header, footer or stamp."""

RUNNING_ARTIFACT_WORDS = 12

EDGE_TOP = 0.12
EDGE_BOTTOM = 0.88
"""Fractions of page height that count as the header and footer zones."""

BOILERPLATE_MAX_WORDS = 25
"""Longer than this and a boilerplate phrase match is assumed to be prose."""

DUPLICATE_MAX_WORDS = 40
"""Only blocks this short are ever dropped as duplicates."""

DUPLICATE_RATIO = 0.85

DUPLICATE_LENGTH_RATIO = 0.95
"""The contract said 0.6, which turned out to block the case it was meant to
catch: a reworded standfirst is nearly the *same* length as the lead it
duplicates, not much shorter. Blast radius is already limited by the candidate
rule below — only quotes, captions and sub-40-word paragraphs are ever
droppable — so this gate only needs to say "meaningfully shorter"."""

LIST_ANNOUNCE_THRESHOLD = 12

# Phrases that survive extraction and should not be read out. Matched against
# short blocks only.
BOILERPLATE_PHRASES = (
    "share this", "share on", "follow us", "sign up for", "sign up to",
    "subscribe to", "subscribe now", "newsletter", "related articles",
    "related stories", "read more", "more from", "most read", "most popular",
    "you might also like", "recommended for you", "all rights reserved",
    "terms of service", "privacy policy", "cookie", "accept all",
    "advertisement", "sponsored content", "log in", "sign in", "register now",
    "view image in fullscreen", "photograph:", "photo:", "image:", "credit:",
    "illustration:", "getty images", "skip to content", "back to top",
    "this article was amended", "do not sell my personal information",
    "comments (", "leave a comment", "about the author",
)

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}

_ZERO_WIDTH = dict.fromkeys(
    [0x00AD, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF], None
)

_SPACES = {
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
}

_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "′": "'", "″": '"',
}

_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff⬀-⯿]+"
)

_SUPERSCRIPT_MARKERS = re.compile(r"[¹²³⁰-⁹]+")

_BRACKET_CITATION = re.compile(r"\s*\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")

_AUTHOR_YEAR = re.compile(
    r"\s*\((?:see\s+)?(?:e\.g\.,?\s*)?"
    r"[A-Z][A-Za-z'’-]+"
    r"(?:\s+(?:et\s+al\.|and|&)\s*[A-Za-z'’-]*)?"
    r"(?:,)?\s*\d{4}[a-z]?"
    r"(?:\s*;\s*[A-Z][A-Za-z'’-]+(?:\s+(?:et\s+al\.|and|&)\s*[A-Za-z'’-]*)?(?:,)?\s*\d{4}[a-z]?)*"
    r"\)"
)

_PAGE_NUMBER = re.compile(r"^[\s\-–—|]*(?:page\s+)?([0-9]{1,4}|[ivxlcdm]{1,7})[\s\-–—|]*$",
                          re.IGNORECASE)

# Preprint and licence stamps. The repetition rule in rule 2 was supposed to
# catch these, and cannot: arXiv stamps the *first page only*, so there is
# nothing to repeat. A narrow pattern is the honest way to remove them.
_STAMP = re.compile(
    r"^\s*(?:arXiv:\s?\d{4}\.\d{4,5}"
    r"|doi:\s?10\.\d{4}"
    r"|https?://doi\.org/"
    r"|preprint\b"
    r"|under review\b"
    r"|to appear in\b"
    r"|licen[cs]ed under\b"
    r"|this work is licen[cs]ed\b"
    r"|permission to make digital\b)",
    re.IGNORECASE,
)

STAMP_MAX_WORDS = 20

_ORDINAL_WORDS = (
    "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen", "Twenty",
)

_TERMINAL = ".!?:;"


@dataclass
class NormalizeOptions:
    """The knobs the CLI exposes."""

    header: bool = True
    keep_captions: bool = False
    strip_citations: bool = False
    is_pdf: bool = False


# --- rule 1: unicode hygiene ----------------------------------------------


def repair_unicode(text: str) -> str:
    """Ligatures, invisible characters, smart quotes, emoji.

    Cheap, and every one of these is an audible glitch: a PDF ligature makes
    "ﬁnd" unpronounceable, a soft hyphen splits a word mid-syllable.
    """
    for ligature, replacement in _LIGATURES.items():
        text = text.replace(ligature, replacement)
    text = text.translate(_ZERO_WIDTH)
    for space, replacement in _SPACES.items():
        text = text.replace(space, replacement)
    for quote, replacement in _QUOTES.items():
        text = text.replace(quote, replacement)
    text = text.replace("…", ".")
    text = _EMOJI.sub("", text)
    text = re.sub(r"^[\s•‣▪◦⁃·]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# --- rule 2: running headers, footers and side stamps ----------------------


def _repeat_key(text: str) -> str:
    """Digits masked, so "Page 3" and "Page 4" collapse to one key."""
    return re.sub(r"\d+", "#", " ".join(text.split()).lower())


def drop_running_artifacts(blocks: list[Block]) -> list[Block]:
    """Remove anything short that repeats across pages.

    Position was the obvious signal and it is not sufficient on its own: the
    arXiv stamp runs down the *left margin*, vertically centred at position
    ~0.30, and no header/footer band catches it. Repetition does. Position is
    kept as a secondary signal that lowers the threshold to two pages.
    """
    pages_by_key: dict[str, set[int]] = {}
    edge_by_key: dict[str, bool] = {}

    for block in blocks:
        if block.page is None or block.word_count() > RUNNING_ARTIFACT_WORDS:
            continue
        key = _repeat_key(block.text)
        if not key:
            continue
        pages_by_key.setdefault(key, set()).add(block.page)
        at_edge = block.position is not None and (
            block.position < EDGE_TOP or block.position > EDGE_BOTTOM
        )
        edge_by_key[key] = edge_by_key.get(key, True) and at_edge

    doomed = {
        key
        for key, pages in pages_by_key.items()
        if len(pages) >= RUNNING_ARTIFACT_PAGES
        or (len(pages) >= 2 and edge_by_key.get(key, False))
    }

    return [
        block
        for block in blocks
        if not (
            block.page is not None
            and block.word_count() <= RUNNING_ARTIFACT_WORDS
            and _repeat_key(block.text) in doomed
        )
    ]


def drop_stamps(blocks: list[Block]) -> list[Block]:
    """Preprint, DOI and licence stamps — PDF only, and short blocks only."""
    return [
        block
        for block in blocks
        if not (
            block.page is not None
            and block.word_count() <= STAMP_MAX_WORDS
            and _STAMP.match(block.text)
        )
    ]


# --- rule 3: standalone page numbers ---------------------------------------


def drop_page_numbers(blocks: list[Block]) -> list[Block]:
    """A block that is nothing but a number, near the top or bottom edge."""
    kept = []
    for block in blocks:
        lonely = _PAGE_NUMBER.match(block.text.strip())
        edge = block.position is not None and (
            block.position < 0.15 or block.position > 0.85
        )
        if lonely and edge:
            continue
        kept.append(block)
    return kept


# --- rules 4 and 5: PDF line breaks ----------------------------------------


def _vocabulary(blocks: list[Block]) -> set[str]:
    words = set()
    for block in blocks:
        for token in block.text.replace("\n", " ").split():
            cleaned = token.strip(".,;:!?()[]\"'").lower()
            if cleaned:
                words.add(cleaned)
    return words


def repair_line_hyphens(text: str, vocabulary: set[str]) -> str:
    """Join words broken across lines — but not genuinely hyphenated ones.

    "representa-\ntion" is one word; "self-\nattention" is two joined by a
    hyphen that belongs there. With no dictionary to hand, the document itself
    is the evidence: if the hyphenated form appears elsewhere, keep the hyphen.
    """

    def choose(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        hyphenated = f"{left}-{right}".lower()
        if hyphenated in vocabulary:
            return f"{left}-{right}"
        return f"{left}{right}"

    return re.sub(r"(\w+)-\n(\w+)", choose, text)


def join_hard_wraps(text: str) -> str:
    """Remaining newlines inside a block are line wrapping, not structure."""
    return re.sub(r"\s*\n\s*", " ", text).strip()


def repair_wrapping(blocks: list[Block]) -> list[Block]:
    vocabulary = _vocabulary(blocks)
    for block in blocks:
        block.text = join_hard_wraps(repair_line_hyphens(block.text, vocabulary))
    return blocks


# --- rule 6: boilerplate sweep ---------------------------------------------


def is_boilerplate(text: str) -> bool:
    """Navigation, share widgets, credits and legal that survived extraction."""
    words = text.split()
    if len(words) > BOILERPLATE_MAX_WORDS:
        return False  # long enough to be real prose
    lowered = text.lower().strip()
    return any(phrase in lowered for phrase in BOILERPLATE_PHRASES)


def drop_boilerplate(blocks: list[Block]) -> list[Block]:
    return [block for block in blocks if not is_boilerplate(block.text)]


# --- rule 7: fuzzy duplicate removal ---------------------------------------


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """Contiguous run — the truncated-pull-quote case."""
    if not needle or len(needle) > len(haystack):
        return False
    first = needle[0]
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start] == first and haystack[start:start + len(needle)] == needle:
            return True
    return False


def drop_duplicates(blocks: list[Block]) -> list[Block]:
    """Remove pull quotes and standfirsts that repeat the body.

    Exact matching does not work: a pull quote is usually a *truncated*
    version of the sentence it quotes, and a standfirst is usually a reworded
    lead. This is the single most likely cause of the acceptance criterion
    "no repeated sentences" failing on a real news article.
    """
    tokenised = [_tokens(block.text) for block in blocks]
    doomed: set[int] = set()

    for index, block in enumerate(blocks):
        if index in doomed:
            continue
        candidate = block.kind in (BlockKind.QUOTE, BlockKind.CAPTION) or (
            block.kind is BlockKind.PARAGRAPH
            and block.word_count() <= DUPLICATE_MAX_WORDS
        )
        if not candidate or not tokenised[index]:
            continue

        for other, other_tokens in enumerate(tokenised):
            if other == index or other in doomed or not other_tokens:
                continue
            if len(other_tokens) <= len(tokenised[index]):
                continue
            if _is_subsequence(tokenised[index], other_tokens):
                doomed.add(index)
                break
            ratio = SequenceMatcher(None, tokenised[index], other_tokens).ratio()
            length_ratio = len(tokenised[index]) / len(other_tokens)
            if ratio >= DUPLICATE_RATIO and length_ratio <= DUPLICATE_LENGTH_RATIO:
                doomed.add(index)
                break

    return [block for index, block in enumerate(blocks) if index not in doomed]


# --- rule 8: drop by kind --------------------------------------------------


def apply_kind_policy(blocks: list[Block], options: NormalizeOptions) -> list[Block]:
    """Captions, tables and code.

    Consecutive omissions collapse into one announcement — PEP 8 has 147 code
    blocks, and "code omitted" 147 times is worse than silence.
    """
    kept: list[Block] = []
    last_was_omission = False

    for block in blocks:
        if block.kind is BlockKind.CAPTION and not options.keep_captions:
            continue
        if block.kind in (BlockKind.TABLE, BlockKind.CODE):
            if not last_was_omission:
                label = "Table omitted." if block.kind is BlockKind.TABLE else "Code omitted."
                kept.append(Block(BlockKind.PARAGRAPH, label, page=block.page))
                last_was_omission = True
            continue
        last_was_omission = False
        kept.append(block)

    return kept


# --- rule 9: citation and footnote markers ---------------------------------


def strip_citation_markers(text: str, author_year: bool = False) -> str:
    """`[1]` and superscripts always; `(Smith 2019)` only when asked.

    Author-year removal is off by default and PDF-only because it collides
    with ordinary parenthetical prose.
    """
    text = _BRACKET_CITATION.sub("", text)
    text = _SUPERSCRIPT_MARKERS.sub("", text)
    if author_year:
        text = _AUTHOR_YEAR.sub("", text)
    return re.sub(r"\s+([.,;:])", r"\1", text).strip()


# --- rules 10 to 12: protect, expand, spell --------------------------------


def _protect(text: str) -> tuple[str, list[str]]:
    stash: list[str] = []

    def hide(match: re.Match[str]) -> str:
        stash.append(match.group(0))
        return f"\x00{len(stash) - 1}\x00"

    for pattern in PROTECTED:
        text = pattern.sub(hide, text)
    return text, stash


def _restore(text: str, stash: list[str]) -> str:
    for index, original in enumerate(stash):
        text = text.replace(f"\x00{index}\x00", original)
    return text


def expand(text: str) -> str:
    """Rule 11 — the expansions in abbreviations.EXPANSIONS, and URLs."""
    text = EMAIL_PATTERN.sub("", text)
    text = URL_PATTERN.sub("link", text)
    text, stash = _protect(text)
    for pattern, replacement in EXPANSIONS:
        text = pattern.sub(replacement, text)
    text = _restore(text, stash)
    return re.sub(r"[ \t]+", " ", text).strip()


def segment_spelled(text: str) -> Speech:
    """Rule 12 — split into Say and Spell nodes on the allowlist."""
    nodes: Speech = []
    cursor = 0

    for match in SPELL_TOKEN.finditer(text):
        token, tail = match.group(1), match.group(2) or ""
        if token not in SPELL_OUT:
            continue
        before = text[cursor:match.start()]
        if before.strip():
            nodes.append(Say(before.strip()))
        nodes.append(Spell(token))
        if tail:
            nodes.append(Say(tail.lstrip("'")))
        cursor = match.end()

    remainder = text[cursor:]
    if remainder.strip():
        nodes.append(Say(remainder.strip()))
    return nodes


def utterances(text: str) -> Speech:
    """Rules 10–12 together: one piece of text becomes Say/Spell nodes."""
    return segment_spelled(expand(text))


# --- rule 13: terminal punctuation -----------------------------------------


def ensure_terminal(text: str) -> str:
    """A heading with no full stop makes the engine run into the next sentence."""
    text = text.strip()
    if not text:
        return text
    return text if text[-1] in _TERMINAL else text + "."


# --- rule 14: the spoken header --------------------------------------------


def _similar(left: str, right: str) -> float:
    return SequenceMatcher(None, _tokens(left), _tokens(right)).ratio()


def header_nodes(document: Document) -> Speech:
    """"*Title*, by *Author*" — what makes a folder of MP3s navigable by ear."""
    nodes: Speech = []
    if document.title:
        nodes += utterances(ensure_terminal(document.title))
        nodes.append(Pause(Pauses.SECTION))
    if document.author:
        nodes += utterances(ensure_terminal(f"By {document.author}"))
        nodes.append(Pause(Pauses.SECTION))
    return nodes


def drop_title_echo(blocks: list[Block], title: str | None) -> list[Block]:
    """The extracted H1 is usually the title again. Say it once."""
    if not title or not blocks:
        return blocks
    first = blocks[0]
    if first.kind is BlockKind.HEADING and _similar(first.text, title) >= 0.8:
        return blocks[1:]
    return blocks


# --- rule 15: structure becomes pauses -------------------------------------


def _ordinal_word(number: int) -> str:
    if 1 <= number <= len(_ORDINAL_WORDS):
        return _ORDINAL_WORDS[number - 1]
    return str(number)


def _tidy(speech: Speech) -> Speech:
    """Merge adjacent pauses, drop empties, and trim the ends."""
    tidied: Speech = []
    for node in speech:
        if isinstance(node, (Say, Spell)):
            if node.text.strip():
                tidied.append(node)
            continue
        if node.ms <= 0:
            continue
        if tidied and isinstance(tidied[-1], Pause):
            tidied[-1] = Pause(max(tidied[-1].ms, node.ms))
        else:
            tidied.append(node)
    while tidied and isinstance(tidied[0], Pause):
        tidied.pop(0)
    while tidied and isinstance(tidied[-1], Pause):
        tidied.pop()
    return tidied


def to_speech(document: Document, blocks: list[Block], options: NormalizeOptions) -> Speech:
    speech: Speech = []
    if options.header:
        speech += header_nodes(document)
        blocks = drop_title_echo(blocks, document.title)

    index = 0
    while index < len(blocks):
        block = blocks[index]

        if block.kind is BlockKind.LIST_ITEM:
            end = index
            while end < len(blocks) and blocks[end].kind is BlockKind.LIST_ITEM:
                end += 1
            run = blocks[index:end]
            if len(run) > LIST_ANNOUNCE_THRESHOLD:
                speech.append(Say(f"A list of {len(run)} items."))
                speech.append(Pause(Pauses.PARAGRAPH))
            for item in run:
                if item.ordinal:
                    speech.append(Say(f"{_ordinal_word(item.ordinal)}."))
                speech += utterances(ensure_terminal(item.text))
                speech.append(Pause(Pauses.LIST_ITEM))
            speech.append(Pause(Pauses.PARAGRAPH))
            index = end
            continue

        if block.kind is BlockKind.HEADING:
            speech.append(
                Pause(Pauses.SECTION if block.level <= 1 else Pauses.HEADING_BEFORE)
            )
            speech += utterances(ensure_terminal(block.text))
            speech.append(Pause(Pauses.HEADING_AFTER))
        else:
            speech += utterances(block.text)
            speech.append(Pause(Pauses.PARAGRAPH))

        index += 1

    return _tidy(speech)


# --- the pipeline ----------------------------------------------------------


def normalize(document: Document, options: NormalizeOptions | None = None) -> Speech:
    """Run every rule, in order. See SPEC.md Appendix A.5."""
    options = options or NormalizeOptions()

    blocks = [
        Block(
            kind=block.kind,
            text=block.text,
            level=block.level,
            ordinal=block.ordinal,
            page=block.page,
            position=block.position,
        )
        for block in document.blocks
    ]

    for block in blocks:                                      # 1
        block.text = repair_unicode(block.text)
    blocks = [block for block in blocks if block.text.strip()]

    blocks = drop_running_artifacts(blocks)                   # 2
    blocks = drop_stamps(blocks)                              # 2b
    blocks = drop_page_numbers(blocks)                        # 3
    blocks = repair_wrapping(blocks)                          # 4, 5
    blocks = drop_boilerplate(blocks)                         # 6
    blocks = drop_duplicates(blocks)                          # 7
    blocks = apply_kind_policy(blocks, options)               # 8

    author_year = options.strip_citations and options.is_pdf  # 9
    for block in blocks:
        block.text = strip_citation_markers(block.text, author_year=author_year)
    blocks = [block for block in blocks if block.text.strip()]

    return to_speech(document, blocks, options)               # 10–15
