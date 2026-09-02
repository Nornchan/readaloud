"""Chunked synthesis: cache, render, stitch, normalize loudness, tag.

Milestone 4 synthesized one request with no chunking, no cache, no
concurrency. This replaces the middle of that pipeline with the real one —
chunk.py decides what to send the backend, cache.py decides whether to send
it at all — while ffmpeg still does the encoding and now also does the
concatenation, loudness normalization, and tagging in a single call per run.

Concurrency is deliberately NOT added here. SPEC.md's original text calls
for "concurrent requests with a bounded worker pool," written with a
rate-limited hosted engine in mind; scripts/bench_synth.py measured that a
single Kokoro inference already saturates all 8 cores via onnxruntime's
`intra_op` parallelism, and that adding a worker pool on top of that
regressed throughput at every configuration tried. Chunks are synthesized
sequentially. `_synthesize_chunks` is written as a plain loop over pure
per-chunk work so a future engine that genuinely benefits from concurrency
(I/O-bound, GPU-parallel, or simply not CPU-saturating on its own) can be
handed a worker pool without restructuring anything above it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from readaloud import cache
from readaloud.chunk import Chunk, chunk_speech
from readaloud.config import Settings
from readaloud.document import Document
from readaloud.engines import TTSBackend
from readaloud.errors import DependencyError, SynthesisError
from readaloud.speech import Speech, render
from readaloud.terminal import Reporter

_CODECS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "m4a": ["-c:a", "aac", "-b:a", "128k"],
    "wav": ["-c:a", "pcm_s16le"],
}

# Every chunk's raw backend output — WAV for Kokoro, AIFF for `say` — is
# decoded to this fixed PCM format before stitching, so chunks from any
# backend and the silence this module generates for A.3 pauses always share
# identical parameters and concatenate cleanly. 24kHz mono matches Kokoro's
# native rate (plenty for speech); other backends are resampled down to it.
INTERMEDIATE_RATE = 24_000

# Single-pass loudnorm, not the more accurate two-pass (measure, then
# correct). Single-pass is the standard choice for a CLI tool synthesizing
# one file per run — two-pass exists for broadcast-grade calibration and
# needs two full passes over the audio, which would roughly double
# synthesis time for a normalization improvement past what most listening
# setups can distinguish. -16 LUFS integrated, matching the spec'd podcast
# convention.
LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise DependencyError(
            "ffmpeg is not installed",
            "Install it with `brew install ffmpeg`.",
        )
    return path


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


def _decode_to_intermediate_wav(raw: bytes) -> bytes:
    """Whatever container the backend produced -> fixed-format PCM WAV bytes."""
    require_ffmpeg()
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", "pipe:0",
        "-ar", str(INTERMEDIATE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
        "-f", "wav", "pipe:1",
    ]
    result = subprocess.run(command, input=raw, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise SynthesisError(
            f"ffmpeg could not decode the synthesized audio: "
            f"{result.stderr.decode('utf-8', 'replace').strip()[:200]}",
            "The backend may have returned an unexpected or empty audio format.",
        )
    return result.stdout


def _write_silence_wav(path: Path, ms: int, params: wave._wave_params) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(params.nchannels)
        handle.setsampwidth(params.sampwidth)
        handle.setframerate(params.framerate)
        frame_count = int(params.framerate * ms / 1000)
        handle.writeframes(b"\x00" * frame_count * params.sampwidth * params.nchannels)


def _synthesize_chunks(
    chunks: list[Chunk],
    backend: TTSBackend,
    voice: str,
    settings: Settings,
    workdir: Path,
    reporter: Reporter,
    use_cache: bool,
) -> list[tuple[Path, int]]:
    """Render, cache, and decode every chunk to an intermediate WAV file.

    Returns [(chunk_wav_path, pause_after_ms), ...] in order, ready to hand
    to the stitcher. Sequential by design — see the module docstring.
    """
    results: list[tuple[Path, int]] = []
    hits = 0

    for index, chunk in enumerate(chunks):
        text = render(list(chunk.nodes), backend.pause_style)
        reporter.progress(index + 1, len(chunks), "synthesizing")
        if not text:
            # A chunk that rendered to nothing (shouldn't happen given how
            # chunk.py builds chunks, but never worth crashing over): carry
            # its trailing pause onto whatever came before, or drop it if
            # this was the very first chunk.
            if results:
                path, previous_pause = results[-1]
                results[-1] = (path, previous_pause + chunk.pause_after_ms)
            continue

        key = cache.chunk_key(text, voice, settings.speed, backend.name)
        wav_bytes = cache.read(key) if use_cache else None
        if wav_bytes is not None:
            hits += 1
        else:
            raw = backend.synthesize(text, voice, settings.speed)
            if not raw:
                raise SynthesisError(
                    f"the {backend.name} backend returned no audio for one chunk",
                    "Try --verbose to see what was sent.",
                )
            wav_bytes = _decode_to_intermediate_wav(raw)
            cache.write(key, wav_bytes)  # refreshed even with --no-cache, like fetch.py

        chunk_path = workdir / f"chunk_{index:05d}.wav"
        chunk_path.write_bytes(wav_bytes)
        results.append((chunk_path, chunk.pause_after_ms))

    reporter.progress_done()
    if hits:
        reporter.stage("cache", f"{hits}/{len(chunks)} chunks reused")
    return results


def _metadata(document: Document | None) -> dict[str, str]:
    """ID3/MP4 tags per SPEC.md section 2: title, artist, album, comment."""
    meta = {"album": "readaloud"}
    if document is None:
        return meta
    if document.title:
        meta["title"] = document.title
    if document.author:
        meta["artist"] = document.author
    source = document.url or (str(document.path) if document.path else "")
    if source:
        meta["comment"] = source
    return meta


def _stitch(
    pieces: list[tuple[Path, int]],
    out_path: Path,
    fmt: str,
    metadata: dict[str, str],
    workdir: Path,
    reporter: Reporter,
) -> None:
    """Concatenate chunks with silence at their A.3 pauses, normalize
    loudness, tag, and encode — one ffmpeg call.

    Falls back to encoding without loudnorm if that call fails: an article
    that is legitimately near-silent throughout (dominated by inserted A.3
    pause silence relative to very little actual speech — a short preview
    of mostly headings, say) asks loudnorm to normalize integrated loudness
    that is at or near -inf LUFS, an undefined/near-infinite gain some
    ffmpeg/libmp3lame builds cannot encode without an internal assertion
    (observed: libmp3lame's psymodel on exactly this input shape). The
    guard in engines/kokoro.py already rejects empty and non-finite chunk
    audio before it ever reaches here, so real speech from Kokoro should
    never legitimately trigger this — but failing the whole run over a
    cosmetic loudness pass, when the ungated audio is otherwise perfectly
    fine, is a worse outcome than a quieter file with a warning.
    """
    require_ffmpeg()
    if not pieces:
        raise SynthesisError("nothing to synthesize")

    with wave.open(str(pieces[0][0]), "rb") as handle:
        params = handle.getparams()

    concat_list = workdir / "concat.txt"
    lines = []
    for index, (chunk_path, pause_ms) in enumerate(pieces):
        lines.append(f"file '{chunk_path}'")
        if pause_ms > 0:
            silence_path = workdir / f"silence_{index:05d}.wav"
            _write_silence_wav(silence_path, pause_ms, params)
            lines.append(f"file '{silence_path}'")
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_args = []
    for key, value in metadata.items():
        if value:
            metadata_args += ["-metadata", f"{key}={value}"]
    codec_args = [*_CODECS.get(fmt, _CODECS["mp3"]), str(out_path)]
    base_command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
    ]

    result = subprocess.run(
        [*base_command, "-af", LOUDNORM_FILTER, *metadata_args, *codec_args],
        capture_output=True,
    )
    if result.returncode != 0 or not out_path.exists():
        first_error = result.stderr.decode("utf-8", "replace").strip()[:200]
        retry = subprocess.run(
            [*base_command, *metadata_args, *codec_args], capture_output=True
        )
        if retry.returncode != 0 or not out_path.exists():
            raise SynthesisError(
                f"ffmpeg could not stitch and encode the audio: {first_error}",
                "Check that your ffmpeg build supports the requested format.",
            )
        reporter.warn(
            f"loudness normalization failed on this audio (near-silent throughout?) "
            f"— wrote it unnormalized instead: {first_error}"
        )


def synthesize_document(
    speech: Speech,
    backend: TTSBackend,
    settings: Settings,
    out_path: Path,
    reporter: Reporter,
    document: Document | None = None,
    use_cache: bool = True,
) -> tuple[Path, float]:
    """The full pipeline: chunk, synthesize (cached), stitch, tag.

    Works identically whether `speech` is the whole document or a
    head()-truncated preview — chunking and stitching don't care how much
    speech there is, so --preview and a full run share this one path.
    """
    voice = resolve_voice(backend, settings)
    warn_on_fallback(backend, voice, settings, reporter)
    reporter.stage("voice", f"{voice} ({backend.name})")

    chunks = chunk_speech(speech, backend.max_chars, backend.pause_style, reporter)
    if not chunks:
        raise SynthesisError(
            "there was nothing to say once the rules had run",
            "Try --keep-text to see what survived normalization.",
        )
    reporter.stage("chunk", f"{len(chunks)} chunk{'s' if len(chunks) != 1 else ''}")

    with tempfile.TemporaryDirectory(prefix="readaloud-") as workdir_name:
        workdir = Path(workdir_name)
        pieces = _synthesize_chunks(
            chunks, backend, voice, settings, workdir, reporter, use_cache
        )
        reporter.stage("stitch", f"{out_path} ({settings.format}, loudnorm -16 LUFS)")
        _stitch(pieces, out_path, settings.format, _metadata(document), workdir, reporter)

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
