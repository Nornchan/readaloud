"""Loads `voices.toml` — the (engine, accent, gender) -> voice-ID table.

Data, not code (SPEC.md section 3): adding a voice, reordering a
preference, or renaming an accent's display name should never require
touching Python. Kept as stdlib `tomllib` rather than YAML+PyYAML — see
SPEC.md Appendix B.6 for why: one config file this size did not justify a
new Homebrew resource, and tomllib is exactly as human-editable at this
scale (comments, nested tables, ordered arrays of records).

Only engines with a fixed, static voice set belong here. `say` does not:
its voices are whatever the local machine has installed, discovered at
runtime via `say -v ?`, so a static table would just go stale.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from readaloud.engines import Voice
from readaloud.errors import AccentUnavailableError


@dataclass(frozen=True)
class VoiceEntry:
    id: str
    accent: str
    gender: str
    grade: str
    """The engine's own quality grade — worth surfacing because the range
    within one engine can be wide."""


@lru_cache(maxsize=1)
def _table() -> dict:
    raw = resources.files("readaloud").joinpath("voices.toml").read_bytes()
    return tomllib.loads(raw.decode("utf-8"))


def _engine_table(engine: str) -> dict:
    try:
        return _table()[engine]
    except KeyError:
        raise KeyError(
            f"no voice table for engine {engine!r} in voices.toml"
        ) from None


def accent_name(accent: str) -> str:
    """The English name for an --accent code, for error messages."""
    return _table()["accent_names"].get(accent, accent)


def supported_accents(engine: str) -> tuple[str, ...]:
    return tuple(_engine_table(engine)["accents"])


def voices(engine: str) -> tuple[VoiceEntry, ...]:
    """Every configured voice, in the file's preference order."""
    return tuple(VoiceEntry(**entry) for entry in _engine_table(engine)["voices"])


def nearest_accent(engine: str, accent: str) -> str:
    """Which supported accent to point at when `accent` isn't one.

    Only for error messages — never used to substitute silently.
    """
    table = _engine_table(engine).get("nearest_accent", {})
    return table.get(accent, supported_accents(engine)[0])


def list_voices(engine: str) -> list[Voice]:
    """Every configured voice, as the `TTSBackend` protocol's `Voice`."""
    return [
        Voice(
            id=entry.id, name=entry.id, accent=entry.accent, gender=entry.gender,
            description=f"grade {entry.grade}",
        )
        for entry in voices(engine)
    ]


def resolve(engine: str, accent: str, gender: str) -> str:
    """Best voice for (accent, gender), or a hard error naming the alternative.

    Deliberately not a silent substitution (SPEC.md Appendix B.3): an accent
    you did not ask for costs a whole run to discover, and unlike a missing
    gender there is a real alternative to offer instead of guessing on the
    user's behalf.
    """
    table = voices(engine)
    supported = supported_accents(engine)
    if accent not in supported:
        nearest = nearest_accent(engine, accent)
        covers = ", ".join(accent_name(a) for a in supported)
        raise AccentUnavailableError(
            f"{engine} has no {accent_name(accent)} English voice — "
            f"it covers {covers} English only",
            f"The nearest it has is --accent {nearest} "
            f"({accent_name(nearest)}). "
            f"Run `readaloud --list-voices --engine {engine}` to see all "
            f"{len(table)} voices.",
        )
    for entry in table:
        if entry.accent == accent and entry.gender == gender:
            return entry.id
    raise AccentUnavailableError(  # pragma: no cover - table covers every pair
        f"{engine} has no {gender} {accent_name(accent)} English voice",
        f"Run `readaloud --list-voices --engine {engine}`.",
    )
