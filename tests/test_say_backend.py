"""The dev backend. macOS-only, and it really does run `say`."""

import shutil
import sys

import pytest

from readaloud.engines import load
from readaloud.engines.say import SayBackend

pytestmark = pytest.mark.skipif(
    not shutil.which("say"), reason="the say backend is macOS-only"
)


@pytest.fixture(scope="module")
def backend():
    return SayBackend()


def test_it_is_registered():
    assert load("say").name == "say"


def test_it_declares_the_say_pause_style(backend):
    assert backend.pause_style == "say"
    assert backend.supports_ssml is False


def test_voices_are_grouped_by_accent(backend):
    voices = backend.list_voices()
    assert voices, "macOS ships at least one English voice"
    accents = {voice.accent for voice in voices}
    assert accents <= {"us", "uk", "au", "ie", "in", "za", "nz", "ca"}
    assert "us" in accents or "uk" in accents


def test_resolve_voice_prefers_an_exact_match(backend):
    voices = backend.list_voices()
    exact = [v for v in voices if v.accent == "uk" and v.gender == "female"]
    if not exact:
        pytest.skip("no en_GB female voice installed")
    assert backend.resolve_voice("uk", "female") in {v.id for v in exact}


def test_resolve_voice_always_returns_something(backend):
    # nz has no macOS voice; it must still fall back rather than crash.
    assert backend.resolve_voice("nz", "male")


def test_synthesize_produces_aiff(backend):
    voice = backend.resolve_voice("uk", "female")
    audio = backend.synthesize("Testing, one two three.", voice, 1.0)
    assert audio[:4] == b"FORM", "AIFF files start with FORM"
    assert len(audio) > 5_000


def test_embedded_pause_commands_lengthen_the_audio(backend):
    """Proof that Pause nodes actually reach the ear."""
    voice = backend.resolve_voice("uk", "female")
    short = backend.synthesize("One two.", voice, 1.0)
    padded = backend.synthesize("One [[slnc 2000]] two.", voice, 1.0)
    assert len(padded) > len(short) * 1.3


def test_a_silent_gender_swap_is_reported(backend, capsys):
    """--gender female quietly returning a male voice makes the flag look broken."""
    from readaloud.config import Settings
    from readaloud.synthesize import warn_on_fallback
    from readaloud.terminal import Reporter

    male = next((v for v in backend.list_voices() if v.gender == "male"), None)
    if male is None:
        pytest.skip("no gendered voice installed")
    reporter = Reporter(stream=sys.stderr)
    warn_on_fallback(
        backend, male.id, Settings(accent=male.accent, gender="female"), reporter
    )
    assert "female voice" in capsys.readouterr().err


def test_no_warning_when_the_voice_matches(backend, capsys):
    from readaloud.config import Settings
    from readaloud.synthesize import warn_on_fallback
    from readaloud.terminal import Reporter

    voice = next((v for v in backend.list_voices() if v.gender and v.accent), None)
    if voice is None:
        pytest.skip("no fully-labelled voice installed")
    warn_on_fallback(
        backend, voice.id,
        Settings(accent=voice.accent, gender=voice.gender),
        Reporter(stream=sys.stderr),
    )
    assert capsys.readouterr().err == ""
