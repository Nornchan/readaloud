"""Single-shot synthesis and encoding.

Deliberately minimal: one request, no chunking, no concurrency, no cache.
Milestone 5 replaces the middle of this with the real pipeline; the ends —
rendering the speech IR for a backend, and encoding what comes back — stay.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from pathlib import Path

from readaloud.config import Settings
from readaloud.engines import TTSBackend
from readaloud.errors import DependencyError, SynthesisError
from readaloud.speech import Pause, Say, Speech, Spell, render
from readaloud.terminal import Reporter

_CODECS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "m4a": ["-c:a", "aac", "-b:a", "128k"],
    "wav": ["-c:a", "pcm_s16le"],
}


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise DependencyError(
            "ffmpeg is not installed",
            "Install it with `brew install ffmpeg`.",
        )
    return path


def encode(raw: bytes, out_path: Path, fmt: str) -> None:
    """Convert whatever the backend returned into the requested format."""
    require_ffmpeg()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", "pipe:0",
        *_CODECS.get(fmt, _CODECS["mp3"]),
        str(out_path),
    ]
    result = subprocess.run(command, input=raw, capture_output=True)
    if result.returncode != 0 or not out_path.exists():
        raise SynthesisError(
            f"ffmpeg could not encode the audio: "
            f"{result.stderr.decode('utf-8', 'replace').strip()[:200]}",
            "Check that your ffmpeg build supports the requested format.",
        )


def duration(path: Path) -> float:
    """Length in seconds, or 0.0 if ffprobe is unavailable."""
    if not shutil.which("ffprobe"):
        return 0.0
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def format_duration(seconds: float) -> str:
    minutes, remainder = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def resolve_voice(backend: TTSBackend, settings: Settings) -> str:
    """--voice wins; otherwise ask the backend for the accent/gender pair."""
    if settings.voice:
        return settings.voice
    chooser = getattr(backend, "resolve_voice", None)
    if chooser is None:
        raise SynthesisError(
            f"the {backend.name} backend cannot map accent and gender to a voice yet",
            "Pass an exact voice with --voice.",
        )
    return chooser(settings.accent, settings.gender)


def warn_on_fallback(
    backend: TTSBackend, voice: str, settings: Settings, reporter: Reporter
) -> None:
    """Say so when the accent/gender pair had to fall back.

    Silently handing back a male voice for --gender female is the kind of
    surprise that makes people think the flag does nothing.
    """
    if settings.voice:
        return
    try:
        chosen = next(
            (entry for entry in backend.list_voices() if entry.id == voice), None
        )
    except Exception:  # a backend that cannot enumerate is not a failure here
        return
    if chosen is None:
        return
    missed = []
    if chosen.accent and chosen.accent != settings.accent:
        missed.append(f"accent {settings.accent}")
    if chosen.gender and chosen.gender != settings.gender:
        missed.append(f"{settings.gender} voice")
    if missed:
        reporter.warn(
            f"the {backend.name} backend has no {' and no '.join(missed)} — "
            f"using {chosen.name} ({chosen.accent or '?'}/{chosen.gender or '?'}) instead"
        )


def _segments(speech: Speech) -> list[tuple[str, object]]:
    """Split the IR into spoken runs and the pauses between them."""
    out: list[tuple[str, object]] = []
    current: Speech = []
    for node in speech:
        if isinstance(node, Pause):
            if current:
                out.append(("say", current))
                current = []
            out.append(("pause", node.ms))
        else:
            current.append(node)
    if current:
        out.append(("say", current))
    return out


def _concat_wav(pieces: list[bytes], pauses: dict[int, int]) -> bytes:
    """Join WAV segments, inserting real silence where the pauses were.

    This is what the `silence` pause style means: an engine that cannot speak
    a pause gets one anyway, because we control the audio. Milestone 5 moves
    the joining to ffmpeg alongside chunk stitching and loudness; the shape of
    it does not change.
    """
    frames: list[bytes] = []
    params = None
    for index, piece in enumerate(pieces):
        with wave.open(io.BytesIO(piece), "rb") as handle:
            if params is None:
                params = handle.getparams()
            frames.append(handle.readframes(handle.getnframes()))
        gap = pauses.get(index)
        if gap:
            silence = b"\x00" * int(
                params.framerate * params.sampwidth * params.nchannels * gap / 1000
            )
            frames.append(silence)

    if params is None:
        raise SynthesisError("nothing to synthesize")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(params.nchannels)
        handle.setsampwidth(params.sampwidth)
        handle.setframerate(params.framerate)
        handle.writeframes(b"".join(frames))
    return buffer.getvalue()


def _synthesize_with_silence(
    speech: Speech,
    backend: TTSBackend,
    voice: str,
    settings: Settings,
    reporter: Reporter,
) -> bytes:
    parts = _segments(speech)
    spoken = [item for kind, item in parts if kind == "say"]
    pieces: list[bytes] = []
    pauses: dict[int, int] = {}

    index = -1
    for kind, item in parts:
        if kind == "pause":
            if index >= 0:
                pauses[index] = pauses.get(index, 0) + int(item)
            continue
        text = render(item, backend.pause_style)
        if not text:
            continue
        index += 1
        reporter.progress(index + 1, len(spoken), "synthesizing")
        pieces.append(backend.synthesize(text, voice, settings.speed))

    reporter.progress_done()
    if not pieces:
        raise SynthesisError(
            "there was nothing to say once the rules had run",
            "Try --keep-text to see what survived normalization.",
        )
    return _concat_wav(pieces, pauses)


def synthesize_once(
    speech: Speech,
    backend: TTSBackend,
    settings: Settings,
    out_path: Path,
    reporter: Reporter,
) -> tuple[Path, float]:
    """Render, synthesize and encode in one pass. Returns (path, seconds)."""
    voice = resolve_voice(backend, settings)
    warn_on_fallback(backend, voice, settings, reporter)
    reporter.stage("voice", f"{voice} ({backend.name})")

    if backend.pause_style == "silence":
        # The engine cannot speak a pause, so we make one.
        raw = _synthesize_with_silence(speech, backend, voice, settings, reporter)
    else:
        text = render(speech, backend.pause_style)
        reporter.stage("synthesize", f"{len(text):,} characters in one request")
        raw = backend.synthesize(text, voice, settings.speed)

    if not raw:
        raise SynthesisError(
            f"the {backend.name} backend returned no audio",
            "Try --verbose to see what was sent.",
        )

    reporter.stage("encode", f"{out_path} ({settings.format})")
    encode(raw, out_path, settings.format)
    return out_path, duration(out_path)


def play(path: Path) -> None:
    """Play a file, for --preview. Silently does nothing without a player."""
    player = shutil.which("afplay") or shutil.which("ffplay")
    if not player:
        return
    command = [player, str(path)]
    if player.endswith("ffplay"):
        command = [player, "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
    subprocess.run(command, capture_output=True)
