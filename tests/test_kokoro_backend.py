"""The Kokoro backend.

Most of these run against the real model, so they are marked `real_engine`
and skipped unless it has already been downloaded — a unit suite must not
pull 156MB. Run `readaloud <anything> --engine kokoro` once to populate it.
"""

import io
import os
import wave
from pathlib import Path

import pytest

from readaloud.engines.kokoro import (
    DEFAULT_VARIANT,
    MODEL_VARIANTS,
    NEAREST_ACCENT,
    SUPPORTED_ACCENTS,
    VOICE_TABLE,
    KokoroBackend,
    models_dir,
)
from readaloud.errors import AccentUnavailableError


# Captured before isolated_home redirects HOME: the model lives in the real
# cache, and a unit test must never download 310MB into a tmpdir.
REAL_ENV = dict(os.environ)
REAL_MODELS = Path(REAL_ENV.get("HOME", "")) / ".cache" / "readaloud" / "models"

needs_model = pytest.mark.skipif(
    not (REAL_MODELS / MODEL_VARIANTS[DEFAULT_VARIANT][0]).exists(),
    reason="Kokoro model not downloaded — run readaloud once with --engine kokoro",
)


@pytest.fixture
def real_cache(monkeypatch):
    """Point the model loader back at the real cache for this test."""
    monkeypatch.setenv("HOME", REAL_ENV["HOME"])
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


class StubEngine:
    """Stands in for the loaded ONNX session, so the output guard can be
    tested without a 310MB download."""

    def __init__(self, samples):
        self._samples = samples

    def create(self, *args, **kwargs):
        return self._samples, 24_000

    def get_voices(self):
        return [entry.id for entry in VOICE_TABLE]


# --- these need no model --------------------------------------------------


def test_defaults_to_the_full_model():
    """fp16 emits all-NaN audio for ~1 in 8 (voice, speed, text) combinations,
    and kokoro trims that to zero frames, so the run silently writes an empty
    file. Nothing that fails silently gets to be the default."""
    assert DEFAULT_VARIANT == "full"
    assert KokoroBackend().variant == "full"


@pytest.mark.parametrize("samples", [[float("nan"), float("nan")], []])
def test_unusable_output_is_caught_rather_than_written(samples):
    """A NaN pass arrives as a zero-length array, because kokoro trims the
    silence it thinks it made. Unguarded, the run 'succeeds' and writes an
    audio file with nothing in it."""
    import numpy as np

    from readaloud.errors import SynthesisError

    backend = KokoroBackend()
    backend._engine = StubEngine(np.array(samples, dtype="float32"))
    with pytest.raises(SynthesisError, match="no usable audio"):
        backend.synthesize("anything", "bf_emma", 1.0)


def test_the_hint_names_the_unstable_variant():
    import numpy as np

    from readaloud.errors import SynthesisError

    backend = KokoroBackend({"model": "fp16"})
    backend._engine = StubEngine(np.array([], dtype="float32"))
    with pytest.raises(SynthesisError) as caught:
        backend.synthesize("anything", "bf_emma", 1.0)
    assert "fp16" in caught.value.hint
    assert 'model = "full"' in caught.value.hint


def test_variant_comes_from_engine_config():
    assert KokoroBackend({"model": "q8f16"}).variant == "q8f16"


def test_it_declares_the_silence_pause_style():
    """Kokoro has no pause markup, so pauses become real audio at stitch time."""
    backend = KokoroBackend()
    assert backend.pause_style == "silence"
    assert backend.supports_ssml is False


def test_the_voice_table_covers_both_accents_and_both_genders():
    pairs = {(entry.accent, entry.gender) for entry in VOICE_TABLE}
    assert pairs == {("us", "female"), ("us", "male"),
                     ("uk", "female"), ("uk", "male")}


def test_english_coverage_is_only_american_and_british():
    assert SUPPORTED_ACCENTS == ("us", "uk")
    assert {entry.accent for entry in VOICE_TABLE} == {"us", "uk"}


@pytest.mark.parametrize("accent", ["au", "ie", "in", "za", "nz", "ca"])
def test_missing_accents_raise_rather_than_substitute(accent):
    with pytest.raises(AccentUnavailableError) as caught:
        KokoroBackend().resolve_voice(accent, "female")
    assert accent not in caught.value.message.split()[-1]  # names the accent, not a voice
    assert f"--accent {NEAREST_ACCENT[accent]}" in caught.value.hint
    assert "--list-voices" in caught.value.hint


def test_the_error_names_the_accent_in_english():
    with pytest.raises(AccentUnavailableError) as caught:
        KokoroBackend().resolve_voice("au", "male")
    assert "Australian" in caught.value.message
    assert caught.value.exit_code == 12


def test_preferred_voices():
    backend = KokoroBackend()
    assert backend.resolve_voice("uk", "female") == "bf_emma"
    assert backend.resolve_voice("us", "female") == "af_heart"
    assert backend.resolve_voice("uk", "male") == "bm_fable"
    assert backend.resolve_voice("us", "male") == "am_michael"


def test_grades_are_surfaced():
    """The range is wide enough that the grade belongs in --list-voices."""
    heart = next(e for e in VOICE_TABLE if e.id == "af_heart")
    adam = next(e for e in VOICE_TABLE if e.id == "am_adam")
    assert heart.grade == "A"
    assert adam.grade == "F+"


# --- these need the model -------------------------------------------------


@pytest.mark.real_engine
@needs_model
def test_synthesizes_valid_wav(real_cache):
    audio = KokoroBackend().synthesize("The tunnel opened in 1843.", "bf_emma", 1.0)
    with wave.open(io.BytesIO(audio), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 24_000
        assert handle.getnframes() > 24_000 // 2


@pytest.mark.real_engine
@needs_model
def test_every_table_voice_exists_in_the_model(real_cache):
    available = {voice.id for voice in KokoroBackend().list_voices()}
    assert available == {entry.id for entry in VOICE_TABLE}


@pytest.mark.real_engine
@needs_model
def test_speed_changes_the_length(real_cache):
    backend = KokoroBackend()
    text = "The Thames Tunnel opened in eighteen forty three."
    slow = backend.synthesize(text, "bf_emma", 0.8)
    fast = backend.synthesize(text, "bf_emma", 1.5)
    assert len(slow) > len(fast) * 1.2
