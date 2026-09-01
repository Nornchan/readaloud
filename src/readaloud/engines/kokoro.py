"""Kokoro-82M via onnxruntime — the default engine.

No API key, no network after the first run, and no PyTorch. That last point is
the reason this file uses kokoro-onnx's default espeak/phonemizer G2P rather
than misaki, which Kokoro's own README recommends: `misaki[en]` resolves to 80
packages including torch 2.13, against 13 for kokoro-onnx alone. Misaki's
advantage is number, symbol and abbreviation handling — and readaloud's
normalizer has already expanded all of that by the time text reaches here, so
the G2P only has to pronounce ordinary words.

The model is downloaded on first use, because 156MB cannot sensibly live in a
Homebrew formula.
"""

from __future__ import annotations

import io
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from readaloud.config import cache_home
from readaloud.engines import Voice
from readaloud.errors import AccentUnavailableError, ModelError, SynthesisError

SAMPLE_RATE = 24_000

# Kokoro handles long input, but a bounded request keeps memory flat and gives
# milestone 5's chunker something to aim at.
MAX_CHARS = 2_000

_HF_BASE = (
    "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/onnx"
)
_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.1/voices-v1.0.bin"
)

# The default is the full fp32 model, not fp16, despite being twice the size.
#
# fp16 is numerically unstable in this export: it emits all-NaN audio for
# roughly one in eight (voice, speed, text) combinations, scattered
# unpredictably rather than confined to a band — and because kokoro-onnx trims
# leading and trailing silence, NaN output arrives as a *zero-length* array,
# so the failure is silent. A 186-case sweep across three voices, two passages
# and 31 speeds: 24 failures on fp16, 0 on fp32.
#
# The f16-derived quantizations inherit the same arithmetic and are assumed
# suspect until swept the same way. All of them stay selectable, because a
# smaller model is a reasonable trade on a constrained machine, but nothing
# that silently produces empty audio gets to be the default.
MODEL_VARIANTS = {
    "full": ("model.onnx", "310 MB"),
    "fp16": ("model_fp16.onnx", "156 MB"),
    "q8f16": ("model_q8f16.onnx", "82 MB"),
    "quantized": ("model_quantized.onnx", "88 MB"),
}
DEFAULT_VARIANT = "full"

UNSTABLE_VARIANTS = frozenset({"fp16", "q8f16"})


@dataclass(frozen=True)
class _KokoroVoice:
    id: str
    accent: str
    gender: str
    grade: str
    """Kokoro's own grade from its VOICES.md — worth surfacing, because the
    range is wide and the British voices are all in the weaker half."""


# Destined for voices.yaml in milestone 6; the ordering within each
# (accent, gender) group is the preference order.
VOICE_TABLE: tuple[_KokoroVoice, ...] = (
    # American English — female
    _KokoroVoice("af_heart", "us", "female", "A"),
    _KokoroVoice("af_bella", "us", "female", "A-"),
    _KokoroVoice("af_nicole", "us", "female", "B-"),
    _KokoroVoice("af_aoede", "us", "female", "C+"),
    _KokoroVoice("af_kore", "us", "female", "C+"),
    _KokoroVoice("af_sarah", "us", "female", "C+"),
    _KokoroVoice("af_alloy", "us", "female", "C"),
    _KokoroVoice("af_nova", "us", "female", "C"),
    _KokoroVoice("af_sky", "us", "female", "C-"),
    _KokoroVoice("af_jessica", "us", "female", "D"),
    _KokoroVoice("af_river", "us", "female", "D"),
    # American English — male
    _KokoroVoice("am_michael", "us", "male", "C+"),
    _KokoroVoice("am_fenrir", "us", "male", "C+"),
    _KokoroVoice("am_puck", "us", "male", "C+"),
    _KokoroVoice("am_echo", "us", "male", "D"),
    _KokoroVoice("am_eric", "us", "male", "D"),
    _KokoroVoice("am_liam", "us", "male", "D"),
    _KokoroVoice("am_onyx", "us", "male", "D"),
    _KokoroVoice("am_santa", "us", "male", "D-"),
    _KokoroVoice("am_adam", "us", "male", "F+"),
    # British English — female
    _KokoroVoice("bf_emma", "uk", "female", "B-"),
    _KokoroVoice("bf_isabella", "uk", "female", "C"),
    _KokoroVoice("bf_alice", "uk", "female", "D"),
    _KokoroVoice("bf_lily", "uk", "female", "D"),
    # British English — male
    _KokoroVoice("bm_fable", "uk", "male", "C"),
    _KokoroVoice("bm_george", "uk", "male", "C"),
    _KokoroVoice("bm_lewis", "uk", "male", "D+"),
    _KokoroVoice("bm_daniel", "uk", "male", "D"),
)

SUPPORTED_ACCENTS = ("us", "uk")

# Which accent a user asking for an unsupported one is nearest to. Used only
# to make the error message useful — never to silently substitute.
NEAREST_ACCENT = {
    "au": "uk", "nz": "uk", "ie": "uk", "za": "uk", "in": "uk", "ca": "us",
}

ACCENT_NAMES = {
    "us": "American", "uk": "British", "au": "Australian", "ie": "Irish",
    "in": "Indian", "za": "South African", "nz": "New Zealand", "ca": "Canadian",
}


def models_dir() -> Path:
    return cache_home() / "models"


def _download(url: str, target: Path, label: str, size_hint: str) -> None:
    """Fetch a model file, with progress, because these are large."""
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    show = sys.stderr.isatty()
    print(f"downloading {label} ({size_hint}) — first run only", file=sys.stderr)

    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            done = 0
            with partial.open("wb") as handle:
                for block in response.iter_bytes(chunk_size=1 << 20):
                    handle.write(block)
                    done += len(block)
                    if show and total:
                        print(
                            f"\r  {done / total:6.1%}  {done >> 20} of {total >> 20} MB",
                            end="", file=sys.stderr,
                        )
        if show:
            print(file=sys.stderr)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise ModelError(
            f"could not download the Kokoro {label}: {exc}",
            "readaloud needs one download before it can work offline. "
            "Check your connection and try again.",
        ) from exc

    partial.replace(target)


def ensure_model(variant: str = DEFAULT_VARIANT) -> tuple[Path, Path]:
    """Model and voice files, downloading them if this is the first run."""
    if variant not in MODEL_VARIANTS:
        raise ModelError(
            f"unknown Kokoro model variant {variant!r}",
            f"Valid variants: {', '.join(MODEL_VARIANTS)}.",
        )
    filename, size_hint = MODEL_VARIANTS[variant]

    model = models_dir() / filename
    if not model.exists():
        _download(f"{_HF_BASE}/{filename}", model, f"{variant} model", size_hint)

    voices = models_dir() / "voices-v1.0.bin"
    if not voices.exists():
        _download(_VOICES_URL, voices, "voice pack", "27 MB")

    return model, voices


def _to_wav(samples: np.ndarray) -> bytes:
    """float32 [-1, 1] to 16-bit PCM WAV bytes."""
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


class KokoroBackend:
    """The `TTSBackend` protocol, backed by Kokoro-82M through onnxruntime."""

    name = "kokoro"
    max_chars = MAX_CHARS
    supports_ssml = False
    pause_style = "silence"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.variant = str((options or {}).get("model", DEFAULT_VARIANT)).lower()
        self._engine = None

    def _load(self):
        if self._engine is not None:
            return self._engine
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:  # pragma: no cover
            raise ModelError(
                "the kokoro-onnx package is not installed",
                "Install it with `pip install kokoro-onnx`.",
            ) from exc
        model, voices = ensure_model(self.variant)
        try:
            self._engine = Kokoro(str(model), str(voices))
        except Exception as exc:
            raise ModelError(
                f"could not load the Kokoro model: {exc}",
                f"The download may be corrupt — delete {models_dir()} and retry.",
            ) from exc
        return self._engine

    def list_voices(self) -> list[Voice]:
        """Only the voices the loaded model actually carries."""
        try:
            available = set(self._load().get_voices())
        except ModelError:
            available = {entry.id for entry in VOICE_TABLE}
        return [
            Voice(
                id=entry.id,
                name=entry.id,
                accent=entry.accent,
                gender=entry.gender,
                description=f"grade {entry.grade}",
            )
            for entry in VOICE_TABLE
            if entry.id in available
        ]

    def resolve_voice(self, accent: str, gender: str) -> str:
        """Best voice for the pair, or a hard error naming the alternative.

        Deliberately not a silent substitution: an accent you did not ask for
        costs a whole run to discover, and unlike a missing gender there is a
        real alternative the user can choose between.
        """
        if accent not in SUPPORTED_ACCENTS:
            nearest = NEAREST_ACCENT.get(accent, "uk")
            raise AccentUnavailableError(
                f"kokoro has no {ACCENT_NAMES.get(accent, accent)} English voice "
                f"— it covers American and British English only",
                f"The nearest it has is --accent {nearest} "
                f"({ACCENT_NAMES[nearest]}). "
                f"Run `readaloud --list-voices --engine kokoro` to see all "
                f"{len(VOICE_TABLE)} voices.",
            )
        for entry in VOICE_TABLE:
            if entry.accent == accent and entry.gender == gender:
                return entry.id
        raise AccentUnavailableError(  # pragma: no cover - table covers all four
            f"kokoro has no {gender} {ACCENT_NAMES[accent]} English voice",
            "Run `readaloud --list-voices --engine kokoro`.",
        )

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        engine = self._load()
        # British voices need the British espeak dictionary or the vowels are
        # wrong even though the style vector is right.
        language = "en-gb" if voice.startswith(("bf_", "bm_")) else "en-us"
        try:
            samples, rate = engine.create(text, voice=voice, speed=speed, lang=language)
        except Exception as exc:
            raise SynthesisError(
                f"kokoro could not synthesize this text: {exc}",
                f"Check that {voice!r} is a valid voice "
                "(`readaloud --list-voices --engine kokoro`).",
            ) from exc
        if rate != SAMPLE_RATE:  # pragma: no cover - kokoro is fixed at 24kHz
            raise SynthesisError(f"unexpected sample rate {rate} from kokoro")

        # Guard the silent failure: a NaN pass comes back as an empty array,
        # because kokoro trims the silence it thinks it produced. Without this
        # the run "succeeds" and writes an audio file with nothing in it.
        if len(samples) == 0 or not np.isfinite(samples).all():
            detail = (
                f" The {self.variant} model is numerically unstable; "
                f"`readaloud config` and set model = \"full\"."
                if self.variant in UNSTABLE_VARIANTS
                else " Try a different --speed."
            )
            raise SynthesisError(
                f"kokoro produced no usable audio for {voice!r} at speed {speed}",
                f"This is a model defect, not a problem with your text.{detail}",
            )
        return _to_wav(samples)
