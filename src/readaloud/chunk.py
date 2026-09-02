"""Chunking: the speech IR becomes backend-sized synthesis requests.

Per SPEC.md Appendix A.3's chunker contract, and the milestone-4 measurement
(scripts/chunk_length_curve.py): **one call per spoken run** — the text
between two A.3 Pause nodes — because merging runs together to amortize
per-call overhead measured 10-25% *slower* at every size tried, while
destroying up to 78% of the pauses a merge crossed. A run is only split when
it alone exceeds the backend's `max_chars`, and then only at sentence
boundaries — never mid-sentence, the prosody break is audible — using the
same abbreviation list the normalizer uses, so "Dr. Smith" is not mistaken
for two sentences here any more than it is there.

The common case (the overwhelming majority of paragraphs, against a 2000-char
budget) returns exactly one chunk per run: this module does nothing to a run
that already fits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from readaloud.abbreviations import ends_sentence
from readaloud.speech import Pause, Say, Speech, Spell, render
from readaloud.terminal import Reporter

# A.3 rule 4: clause-level punctuation to split an oversized *sentence* on,
# tried in this order of preference — the rightmost match under the limit
# wins regardless of which character it is; em dash is last because it is
# the least reliable clause boundary in ordinary prose (also used as a
# stylistic aside-marker, not always a clause break).
_CLAUSE_CHARS = ";:,—"

_WORD_BREAK = re.compile(r"\s+")

Node = Say | Spell


@dataclass(frozen=True)
class Chunk:
    """One `synthesize()` call's worth of text, and what follows it."""

    nodes: tuple[Node, ...]
    pause_after_ms: int
    """The real A.3 pause that follows this chunk's audio, applied as
    stitch-time silence. 0 when this chunk is a mid-run split and the next
    chunk is a direct continuation of the same run — there was never a pause
    there to preserve; A.3's own vocabulary agrees (`SENTENCE = 0`, "engines
    handle terminal punctuation themselves")."""


def _render_len(nodes: tuple[Node, ...] | list[Node], pause_style: str) -> int:
    return len(render(list(nodes), pause_style))


def _plain_text(nodes: tuple[Node, ...]) -> str:
    return " ".join(node.text for node in nodes)


def _split_run_into_sentences(nodes: tuple[Node, ...]) -> list[tuple[Node, ...]]:
    """One run's nodes, grouped into complete sentences.

    A Spell node is atomic and never ends a sentence on its own — the
    normalizer's segment_spelled() always keeps trailing punctuation in its
    own Say node, so a spelled acronym never has a "." glued to it. A Say
    node's text can span zero, one, or several sentence ends ("First
    sentence. Second sentence." is one Say node after expand()), so
    splitting happens word by word inside Say text, using the same
    abbreviation-aware check the chunker contract requires (A.4-A): a "."
    after "Dr." or "U." does not end a sentence.

    Re-tokenizing to one Say node per word does not change what gets sent to
    the backend — render() joins nodes with a single space regardless of how
    many there are — so this is purely an internal bookkeeping choice, not a
    change in synthesized output.
    """
    sentences: list[list[Node]] = [[]]
    for node in nodes:
        if isinstance(node, Spell):
            sentences[-1].append(node)
            continue
        for word in _WORD_BREAK.split(node.text.strip()):
            if not word:
                continue
            sentences[-1].append(Say(word))
            if ends_sentence(word):
                sentences.append([])
    return [tuple(s) for s in sentences if s]


def _find_split_point(nodes: list[Node], max_chars: int, pause_style: str) -> tuple[int, bool]:
    """The node index to split *before*, and whether it's the rule-4 fallback.

    Lengths are measured per node and summed rather than jointly rendered —
    an approximation that slightly *overestimates* the joined length (render()
    adds one space between nodes; summing double-counts that), which only
    makes the guard more conservative. Fine for a soft internal budget;
    max_chars is our own constant, not an API-enforced wire limit.
    """
    cumulative = 0
    last_clause_index = 0
    last_word_index = 0
    for index, node in enumerate(nodes):
        piece_len = _render_len((node,), pause_style)
        if cumulative + piece_len > max_chars:
            break
        cumulative += piece_len + 1
        last_word_index = index + 1
        if isinstance(node, Say) and node.text and node.text[-1] in _CLAUSE_CHARS:
            last_clause_index = index + 1
    if last_clause_index:
        return last_clause_index, False
    if last_word_index:
        return last_word_index, True
    return 0, True


def _split_oversized_sentence(
    nodes: tuple[Node, ...], max_chars: int, pause_style: str
) -> list[tuple[tuple[Node, ...], bool]]:
    """A.3 rule 4. Returns [(piece, was_truncated_at_a_word_boundary), ...]."""
    pieces: list[tuple[tuple[Node, ...], bool]] = []
    remaining = list(nodes)

    while _render_len(remaining, pause_style) > max_chars:
        split_at, truncated = _find_split_point(remaining, max_chars, pause_style)
        if split_at <= 0:
            # Not even the first node fits alone (a single pathologically
            # long token) — nothing left to cut; send it as-is rather than
            # loop forever or drop text.
            break
        pieces.append((tuple(remaining[:split_at]), truncated))
        remaining = remaining[split_at:]

    if remaining:
        pieces.append((tuple(remaining), False))
    return pieces


def chunk_run(
    nodes: tuple[Node, ...],
    max_chars: int,
    pause_style: str,
    reporter: Reporter | None = None,
) -> list[list[Node]]:
    """One spoken run, packed into 1+ backend-sized pieces.

    The common case returns a single piece: the whole run, unsplit.
    Multiple sentences are packed greedily up to max_chars when a run must
    be split at all — this crosses no A.3 pause boundary (there is never a
    pause between two sentences of the same paragraph; Pauses.SENTENCE is
    0), so it costs nothing the chunk-length curve measured merging as
    costing. It only reduces call count for the rare oversized run.
    """
    if _render_len(nodes, pause_style) <= max_chars:
        return [list(nodes)]

    sentences = _split_run_into_sentences(nodes)
    pieces: list[list[Node]] = []
    current: list[Node] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            pieces.append(current)
        current, current_len = [], 0

    for sentence in sentences:
        sentence_len = _render_len(sentence, pause_style)

        if sentence_len > max_chars:
            flush()
            for sub, was_truncated in _split_oversized_sentence(sentence, max_chars, pause_style):
                pieces.append(list(sub))
                if was_truncated and reporter is not None:
                    preview = _plain_text(sub)[:60]
                    reporter.stage(
                        "chunk",
                        f"no clause boundary in an oversized sentence — split at "
                        f"a word boundary near {preview!r}…",
                    )
            continue

        joined_len = current_len + (1 if current else 0) + sentence_len
        if current and joined_len > max_chars:
            flush()
            joined_len = sentence_len
        current.extend(sentence)
        current_len = joined_len

    flush()
    return pieces


def chunk_speech(
    speech: Speech,
    max_chars: int,
    pause_style: str,
    reporter: Reporter | None = None,
) -> list[Chunk]:
    """The whole document, split at Pause boundaries first (A.3 rule 1) and
    only then, inside an oversized run, at sentence boundaries (rule 4)."""
    chunks: list[Chunk] = []
    current_run: list[Node] = []

    def flush(trailing_pause_ms: int) -> None:
        nonlocal current_run
        if not current_run:
            return
        pieces = chunk_run(tuple(current_run), max_chars, pause_style, reporter)
        for index, piece in enumerate(pieces):
            is_last = index == len(pieces) - 1
            chunks.append(
                Chunk(
                    nodes=tuple(piece),
                    pause_after_ms=trailing_pause_ms if is_last else 0,
                )
            )
        current_run = []

    for node in speech:
        if isinstance(node, Pause):
            # normalize()'s _tidy() merges adjacent pauses and trims leading
            # and trailing ones, so a Pause always follows a non-empty run
            # in well-formed input; flush() is a no-op if that ever isn't
            # true rather than raising, since a malformed IR is not this
            # module's failure to report.
            flush(node.ms)
        else:
            current_run.append(node)
    flush(0)  # whatever is left when speech ends, with nothing left to wait for

    return chunks
