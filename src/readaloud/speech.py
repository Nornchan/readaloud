"""The speech IR — what the normalizer emits and the backends render.

Three node types, per SPEC.md Appendix A.3. Deliberately engine-neutral: the
normalizer never writes SSML, because Azure takes SSML, Eleven v3 dropped it
for `[pause]` tags, macOS `say` uses `[[slnc]]`, and Piper has nothing at all.
Each backend renders these nodes in its own dialect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Say:
    """Text spoken as written."""

    text: str


@dataclass(frozen=True)
class Spell:
    """Text spoken letter by letter — "US" as "U. S.", not "us"."""

    text: str


@dataclass(frozen=True)
class Pause:
    """Silence, in milliseconds."""

    ms: int


Utterance = Say | Spell | Pause
Speech = list[Utterance]

# How a backend wants pauses expressed.
PauseStyle = Literal["ssml", "tag", "silence", "say"]


class Pauses:
    """Named durations, so tuning happens in one place."""

    SENTENCE = 0        # engines handle terminal punctuation themselves
    LIST_ITEM = 300
    HEADING_AFTER = 400
    PARAGRAPH = 500
    HEADING_BEFORE = 900
    SECTION = 1200


def word_count(speech: Speech) -> int:
    return sum(
        len(node.text.split())
        for node in speech
        if isinstance(node, (Say, Spell))
    )


def char_count(speech: Speech) -> int:
    return sum(
        len(node.text) for node in speech if isinstance(node, (Say, Spell))
    )


def duration_estimate(speech: Speech, words_per_minute: float, speed: float) -> float:
    """Minutes, counting the pauses — they add up over a long article."""
    spoken = word_count(speech) / (words_per_minute * speed)
    silence = sum(node.ms for node in speech if isinstance(node, Pause)) / 60_000
    return spoken + silence


def head(speech: Speech, words: int) -> Speech:
    """The first `words` words, cut at a node boundary. For --preview."""
    taken: Speech = []
    seen = 0
    for node in speech:
        if isinstance(node, Pause):
            taken.append(node)
            continue
        count = len(node.text.split())
        if seen + count > words and taken:
            break
        taken.append(node)
        seen += count
        if seen >= words:
            break
    return taken


def _letters(text: str) -> str:
    """"US" → "U. S." — the fallback for backends with no say-as."""
    return ". ".join(character for character in text if character.strip()) + "."


def transcript(speech: Speech) -> str:
    """A readable rendering for --keep-text.

    Pauses become paragraph breaks, so what you read has the same shape as
    what you hear.
    """
    lines: list[str] = []
    current: list[str] = []
    for node in speech:
        if isinstance(node, Say):
            current.append(node.text)
        elif isinstance(node, Spell):
            current.append(node.text)
        elif node.ms >= Pauses.HEADING_AFTER:
            if current:
                lines.append(" ".join(current))
                current = []
            if node.ms >= Pauses.HEADING_BEFORE:
                lines.append("")
    if current:
        lines.append(" ".join(current))
    return "\n\n".join(line for line in lines if line != "") + "\n"


def serialize(speech: Speech) -> str:
    """The golden-fixture format: one node per line, diffable.

    A rule change shows up as a line-level diff across the whole corpus, which
    is the point — see scripts/golden.py.
    """
    lines = []
    for node in speech:
        if isinstance(node, Say):
            lines.append(f"SAY   {node.text}")
        elif isinstance(node, Spell):
            lines.append(f"SPELL {node.text}")
        else:
            lines.append(f"PAUSE {node.ms}")
    return "\n".join(lines) + "\n"


def _escape_ssml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render(speech: Speech, style: PauseStyle) -> str:
    """Render the IR into whatever the backend actually accepts."""
    parts: list[str] = []
    for node in speech:
        if isinstance(node, Say):
            parts.append(_escape_ssml(node.text) if style == "ssml" else node.text)
        elif isinstance(node, Spell):
            if style == "ssml":
                parts.append(
                    f'<say-as interpret-as="characters">{_escape_ssml(node.text)}</say-as>'
                )
            elif style == "say":
                parts.append(f"[[char LTRL]]{node.text}[[char NORM]]")
            else:
                parts.append(_letters(node.text))
        else:
            if node.ms <= 0:
                continue
            if style == "ssml":
                parts.append(f'<break time="{node.ms}ms"/>')
            elif style == "say":
                parts.append(f"[[slnc {node.ms}]]")
            elif style == "tag":
                if node.ms >= Pauses.HEADING_BEFORE:
                    parts.append("[long pause]")
                elif node.ms >= Pauses.PARAGRAPH:
                    parts.append("[pause]")
                else:
                    parts.append("[short pause]")
            # style == "silence": the pause becomes real silence at stitch
            # time, so it contributes nothing to the text sent to the engine.
    return re.sub(r"  +", " ", " ".join(part for part in parts if part)).strip()
