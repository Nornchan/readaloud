"""The chunk -> cache -> stitch -> tag pipeline, against FakeBackend.

FakeBackend always returns exactly 0.1s of digital silence per call
regardless of what text it's given, which makes duration arithmetic
predictable (n chunks -> n * 0.1s of speech, plus whatever A.3 pause
silence got inserted) and, as a bonus, is exactly the pathological
near-silent-throughout input that exercises the loudnorm fallback for free.
"""

from pathlib import Path

import pytest
from conftest import FakeBackend

from readaloud import cache
from readaloud.config import Settings
from readaloud.document import Document
from readaloud.speech import Pause, Say
from readaloud.synthesize import (
    _metadata,
    duration,
    format_duration,
    synthesize_document,
)
from readaloud.terminal import Reporter


def settings(**overrides):
    base = dict(engine="fake", accent="uk", gender="female", speed=1.0, format="mp3")
    base.update(overrides)
    return Settings(**base)


def reporter():
    return Reporter(verbose=False)


def test_produces_a_playable_file(tmp_path):
    speech = [Say("Hello there."), Pause(500), Say("Goodbye now.")]
    out = tmp_path / "out.mp3"
    path, seconds = synthesize_document(speech, FakeBackend(), settings(), out, reporter())
    assert path == out
    assert path.exists() and path.stat().st_size > 0
    assert seconds > 0


def test_duration_reflects_chunks_and_pauses(tmp_path):
    # Two chunks (separated by a real pause) => 2 * 0.1s speech + 0.5s pause,
    # loose bounds because mp3 encoding pads slightly.
    speech = [Say("First."), Pause(500), Say("Second.")]
    out = tmp_path / "out.mp3"
    _, seconds = synthesize_document(speech, FakeBackend(), settings(), out, reporter())
    assert 0.5 < seconds < 1.2


def test_cache_is_reused_on_a_second_run(tmp_path):
    speech = [Say("Cache me if you can.")]
    backend = FakeBackend()
    out1 = tmp_path / "one.mp3"
    synthesize_document(speech, backend, settings(), out1, reporter())
    assert len(backend.calls) == 1

    backend2 = FakeBackend()  # a fresh instance -- proves it's the disk cache, not object state
    out2 = tmp_path / "two.mp3"
    synthesize_document(speech, backend2, settings(), out2, reporter())
    assert len(backend2.calls) == 0  # the chunk was served from cache, not resynthesized


def test_no_cache_bypasses_the_read(tmp_path):
    speech = [Say("Fresh every time.")]
    backend = FakeBackend()
    settings_obj = settings()
    synthesize_document(speech, backend, settings_obj, tmp_path / "a.mp3", reporter())
    assert len(backend.calls) == 1

    backend2 = FakeBackend()
    synthesize_document(
        speech, backend2, settings_obj, tmp_path / "b.mp3", reporter(), use_cache=False
    )
    assert len(backend2.calls) == 1  # not served from cache


def test_no_cache_still_refreshes_the_entry(tmp_path):
    """Mirrors fetch.py: --no-cache skips the read but still writes, so the
    NEXT normal run benefits."""
    speech = [Say("Refresh me.")]
    settings_obj = settings()
    synthesize_document(
        speech, FakeBackend(), settings_obj, tmp_path / "a.mp3", reporter(), use_cache=False
    )
    backend2 = FakeBackend()
    synthesize_document(speech, backend2, settings_obj, tmp_path / "b.mp3", reporter())
    assert len(backend2.calls) == 0  # served from the entry --no-cache just wrote


def test_different_voice_or_speed_is_not_a_cache_hit(tmp_path):
    speech = [Say("Same text.")]
    backend = FakeBackend()
    synthesize_document(speech, backend, settings(), tmp_path / "a.mp3", reporter())
    synthesize_document(
        speech, backend, settings(speed=1.5), tmp_path / "b.mp3", reporter()
    )
    assert len(backend.calls) == 2


def test_metadata_is_written(tmp_path):
    document = Document(
        title="The Tunnel", author="Jane Marlow", url="https://example.com/tunnel"
    )
    speech = [Say("Content.")]
    out = tmp_path / "out.mp3"
    synthesize_document(speech, FakeBackend(), settings(), out, reporter(), document=document)

    import subprocess

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags",
         "-of", "default=noprint_wrappers=1", str(out)],
        capture_output=True, text=True,
    )
    assert "TAG:title=The Tunnel" in result.stdout
    assert "TAG:artist=Jane Marlow" in result.stdout
    assert "TAG:album=readaloud" in result.stdout
    assert "TAG:comment=https://example.com/tunnel" in result.stdout


def test_metadata_helper_skips_missing_fields():
    assert _metadata(None) == {"album": "readaloud"}
    assert _metadata(Document()) == {"album": "readaloud"}
    meta = _metadata(Document(title="T", path=Path("/tmp/a.pdf")))
    assert meta["title"] == "T"
    assert meta["comment"] == "/tmp/a.pdf"
    assert "artist" not in meta


def test_url_wins_over_path_for_the_comment_tag():
    document = Document(url="https://example.com/x", path=Path("/tmp/a.html"))
    assert _metadata(document)["comment"] == "https://example.com/x"


def test_near_silent_audio_falls_back_from_loudnorm_without_failing(tmp_path, capsys):
    """FakeBackend's output is pure digital silence throughout -- exactly
    the shape that makes loudnorm ask for undefined/infinite gain and crash
    libmp3lame's psymodel. The run must still produce a usable file."""
    speech = [Say("Silence."), Pause(500), Say("More silence.")]
    out = tmp_path / "quiet.mp3"
    warn_reporter = Reporter(verbose=False)
    path, seconds = synthesize_document(speech, FakeBackend(), settings(), out, warn_reporter)
    assert path.exists() and path.stat().st_size > 0
    assert seconds > 0


def test_empty_speech_raises_a_clear_error(tmp_path):
    from readaloud.errors import SynthesisError

    with pytest.raises(SynthesisError, match="nothing to say"):
        synthesize_document([], FakeBackend(), settings(), tmp_path / "out.mp3", reporter())


def test_a_preview_sized_speech_goes_through_the_same_path(tmp_path):
    """--preview truncates speech before calling this function; the
    function itself doesn't need to know."""
    from readaloud.speech import head

    long_speech = [Say(f"Sentence {n}.") for n in range(50)]
    short = head(long_speech, 5)
    out = tmp_path / "preview.mp3"
    path, seconds = synthesize_document(short, FakeBackend(), settings(), out, reporter())
    assert path.exists()
    assert seconds > 0


def test_format_duration_hours():
    assert format_duration(45) == "0:45"
    assert format_duration(125) == "2:05"
    assert format_duration(3725) == "1:02:05"


def test_duration_of_a_missing_file_is_zero(tmp_path):
    assert duration(tmp_path / "nope.mp3") == 0.0
