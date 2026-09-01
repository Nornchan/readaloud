"""Single-shot synthesis and encoding.

Deliberately minimal: one request, no chunking, no concurrency, no cache.
Milestone 5 replaces the middle of this with the real pipeline; the ends —
rendering the speech IR for a backend, and encoding what comes back — stay.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from readaloud.config import Settings
from readaloud.engines import TTSBackend
from readaloud.errors import DependencyError, SynthesisError
from readaloud.speech import Speech, render
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
    text = render(speech, backend.pause_style)

    reporter.stage("voice", f"{voice} ({backend.name})")
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
