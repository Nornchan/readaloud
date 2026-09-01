"""TTS backend protocol and registry.

Adding a backend is meant to be one new file plus one line in `_REGISTRY`.
The registry stores *metadata only* and imports the implementing module lazily,
so `readaloud --help` never pays for importing a synthesis stack, and a backend
whose optional dependency isn't installed doesn't break the whole CLI.

The backends themselves land in milestones 4 and 6; this module defines the
contract they implement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from readaloud.errors import UsageError


@dataclass(frozen=True)
class Voice:
    """One concrete voice offered by a backend."""

    id: str
    name: str
    accent: str | None = None
    gender: str | None = None
    description: str = ""


@runtime_checkable
class TTSBackend(Protocol):
    """What every backend must provide."""

    name: str
    max_chars: int
    supports_ssml: bool
    pause_style: str
    """How this engine wants pauses expressed — see readaloud.speech.render."""

    def list_voices(self) -> list[Voice]:
        """Every voice this backend can use, for --list-voices."""
        ...

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        """Render one chunk of text to encoded audio bytes."""
        ...


@dataclass(frozen=True)
class EngineSpec:
    """How to find and describe a backend without importing it."""

    name: str
    module: str
    factory: str
    summary: str
    development: bool = False
    """Not a shipping engine — exists to make rule changes audible."""


_REGISTRY: dict[str, EngineSpec] = {
    "kokoro": EngineSpec(
        name="kokoro",
        module="readaloud.engines.kokoro",
        factory="KokoroBackend",
        summary="local neural TTS (Kokoro-82M via onnxruntime) — no network, no key",
    ),
    "say": EngineSpec(
        name="say",
        module="readaloud.engines.say",
        factory="SayBackend",
        summary="macOS `say` — development only, for checking how rules sound",
        development=True,
    ),
}


def engine_names() -> list[str]:
    return sorted(_REGISTRY)


def spec(name: str) -> EngineSpec:
    """Look up a backend's metadata, or fail naming the valid options."""
    found = _REGISTRY.get(name.lower())
    if found is None:
        raise UsageError(
            f"unknown engine {name!r}",
            f"Available engines: {', '.join(engine_names())}.",
        )
    return found


def load(name: str, options: dict | None = None) -> TTSBackend:
    """Import and instantiate a backend by name.

    `options` is the engine's own table from the config file, e.g.
    [engines.kokoro] model = "q8f16".
    """
    found = spec(name)
    try:
        module = __import__(found.module, fromlist=[found.factory])
    except ImportError as exc:  # pragma: no cover - until milestone 4 lands
        raise UsageError(
            f"the {found.name} backend is not available yet",
            "Synthesis backends arrive in a later milestone; "
            "`readaloud --estimate` and `--keep-text` work without one.",
        ) from exc
    factory = getattr(module, found.factory)
    try:
        return factory(options or {})
    except TypeError:
        return factory()  # backends that take no configuration
