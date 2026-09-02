#!/usr/bin/env python3
"""Measure what actually moves Kokoro's synthesis speed.

Three levers, measured rather than assumed:
  1. ONNX Runtime execution provider (CPU vs CoreML)
  2. intra_op_num_threads (parallelism *inside* one inference)
  3. chunk-level parallelism (parallelism *across* independent chunks)

Every run synthesizes the same chunk list and is checked against the
sequential baseline for correctness, because a faster wrong answer is not a
result -- espeak-ng in particular is not obviously reentrant.

    python scripts/bench_synth.py
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import onnxruntime as rt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kokoro_onnx import Kokoro  # noqa: E402

from corpus import CORPUS  # noqa: E402
from readaloud.engines.kokoro import SAMPLE_RATE, models_dir  # noqa: E402
from readaloud.extract import extract  # noqa: E402
from readaloud.normalize import normalize  # noqa: E402
from readaloud.source import detect  # noqa: E402
from readaloud.speech import Pause, Say, Spell, head  # noqa: E402

VOICE = "bf_emma"
LANG = "en-gb"
TARGET_WORDS = 600
CHUNK_CHARS = 500


def _split(text: str, limit: int) -> list[str]:
    """Sentence-boundary split, standing in for milestone 5's chunker."""
    import re

    sentences = re.split(r"(?<=[.!?]) +", text)
    out, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > limit:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def workload() -> list[str]:
    """Realistic chunks: the real pipeline's spoken runs, not lorem ipsum.

    Split to CHUNK_CHARS so the chunk count reflects what milestone 5 would
    actually hand a worker pool. Measuring parallelism over two chunks would
    say nothing.
    """
    document = extract(detect(str(CORPUS / "html" / "wikipedia.html")))
    speech = head(normalize(document), TARGET_WORDS)
    runs, current = [], []
    for node in speech:
        if isinstance(node, Pause):
            if current:
                runs.append(" ".join(current))
                current = []
        elif isinstance(node, (Say, Spell)):
            current.append(node.text)
    if current:
        runs.append(" ".join(current))

    chunks: list[str] = []
    for run_text in runs:
        chunks.extend(_split(run_text, CHUNK_CHARS))
    return [chunk for chunk in chunks if len(chunk) > 20]


def build(provider: str, intra: int | None) -> Kokoro:
    options = rt.SessionOptions()
    if intra is not None:
        options.intra_op_num_threads = intra
    session = rt.InferenceSession(
        str(models_dir() / "model.onnx"),
        sess_options=options,
        providers=[provider],
    )
    return Kokoro.from_session(session, str(models_dir() / "voices-v1.0.bin"))


def run(engine: Kokoro, chunks: list[str], workers: int) -> tuple[float, list[np.ndarray]]:
    def one(text: str) -> np.ndarray:
        samples, _ = engine.create(text, voice=VOICE, speed=1.0, lang=LANG)
        return samples

    started = time.perf_counter()
    if workers == 1:
        out = [one(chunk) for chunk in chunks]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            out = list(pool.map(one, chunks))
    return time.perf_counter() - started, out


def audio_seconds(pieces: list[np.ndarray]) -> float:
    return sum(len(piece) for piece in pieces) / SAMPLE_RATE


def matches(baseline: list[np.ndarray], other: list[np.ndarray]) -> str:
    if len(baseline) != len(other):
        return "COUNT MISMATCH"
    for want, got in zip(baseline, other):
        if len(got) == 0 or not np.isfinite(got).all():
            return "EMPTY/NaN"
        if abs(len(want) - len(got)) > 0.02 * max(len(want), 1):
            return "LENGTH DRIFT"
    return "ok"


CONFIGS = [
    ("CPU   default  seq", "CPUExecutionProvider", None, 1),
    ("CPU   intra=1   seq", "CPUExecutionProvider", 1, 1),
    ("CPU   intra=8   seq", "CPUExecutionProvider", 8, 1),
    ("CoreML          seq", "CoreMLExecutionProvider", None, 1),
    ("CPU   intra=1   x4 ", "CPUExecutionProvider", 1, 4),
    ("CPU   intra=1   x8 ", "CPUExecutionProvider", 1, 8),
    ("CPU   intra=2   x4 ", "CPUExecutionProvider", 2, 4),
    ("CPU   default   x4 ", "CPUExecutionProvider", None, 4),
    ("CoreML          x4 ", "CoreMLExecutionProvider", None, 4),
]


def main() -> int:
    chunks = workload()
    chars = sum(len(chunk) for chunk in chunks)
    print(f"{len(chunks)} chunks, {chars:,} characters, voice {VOICE}\n")

    baseline_audio: list[np.ndarray] | None = None
    baseline_wall = None

    print(f"{'configuration':<22}{'wall':>8}{'xRT':>8}{'speedup':>9}  correctness")
    print("-" * 62)
    for label, provider, intra, workers in CONFIGS:
        try:
            engine = build(provider, intra)
            engine.create("warm up the session.", voice=VOICE, speed=1.0, lang=LANG)
            wall, pieces = run(engine, chunks, workers)
        except Exception as exc:
            print(f"{label:<22}{'—':>8}{'—':>8}{'—':>9}  FAILED: {str(exc)[:40]}")
            continue

        seconds = audio_seconds(pieces)
        xrt = wall / seconds if seconds else float("nan")
        if baseline_audio is None:
            baseline_audio, baseline_wall = pieces, wall
            verdict = "baseline"
        else:
            verdict = matches(baseline_audio, pieces)
        speedup = baseline_wall / wall if baseline_wall else 1.0
        print(f"{label:<22}{wall:>7.1f}s{xrt:>8.2f}{speedup:>8.2f}x  {verdict}")

        if baseline_audio is pieces:
            print(f"{'':<22}{'':>8}{'':>8}{'':>9}  "
                  f"{chars / seconds:.1f} chars/s of audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
