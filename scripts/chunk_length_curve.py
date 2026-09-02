#!/usr/bin/env python3
"""How synthesis speed and pause fidelity trade off against chunk size.

Milestone 4 synthesizes one call per spoken run (the text between two
A.3 Pause nodes) and inserts the pause as real silence at the stitch
boundary. Making a chunk span *more* than one run -- merging paragraphs
into a single synthesize() call to amortize per-call overhead -- means the
Pause between them has nowhere to live: it wasn't spoken by the engine (we
control the audio, but we only control it at stitch boundaries), so it is
simply gone from the output.

This script measures both sides of that trade for one fixed passage:
  - wall-clock speedup at chunk sizes 100/250/500/1000/2000 chars, chunking
    *across* run boundaries the way a naive "bigger chunks" change would
  - what fraction of the passage's original A.3 pauses survive at each size,
    because a chunk boundary must land exactly on a pause boundary for it to
    be preserved

It does not change any shipped chunking behaviour. It answers a question.

    python scripts/chunk_length_curve.py
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from corpus import CORPUS  # noqa: E402

from readaloud.engines.kokoro import KokoroBackend  # noqa: E402
from readaloud.extract import extract  # noqa: E402
from readaloud.normalize import normalize  # noqa: E402
from readaloud.source import detect  # noqa: E402
from readaloud.speech import Pause, Say, Spell, head  # noqa: E402

VOICE = "bf_emma"
LANG = "en-gb"
TARGET_WORDS = 700  # enough headings/paragraphs/list items to get real pause variety
CHUNK_SIZES = (100, 250, 500, 1000, 2000)
SOURCE = CORPUS / "html" / "wikipedia.html"


@dataclass
class Boundary:
    """One point between two spoken runs, carrying the A.3 pause that sat there."""
    text_before: str
    pause_ms: int


def flatten(speech) -> tuple[list[str], list[int]]:
    """The passage as (runs, pause_after_each_run_except_the_last)."""
    runs: list[str] = []
    pauses: list[int] = []
    current: list[str] = []
    for node in speech:
        if isinstance(node, Pause):
            if current:
                runs.append(" ".join(current))
                current = []
                pauses.append(node.ms)
            elif pauses:
                pauses[-1] = max(pauses[-1], node.ms)
        elif isinstance(node, (Say, Spell)):
            current.append(node.text)
    if current:
        runs.append(" ".join(current))
    # one fewer boundary than run, unless a pause trailed the last run
    pauses = pauses[: len(runs) - 1] if len(pauses) >= len(runs) else pauses
    return runs, pauses


def _split(text: str, limit: int) -> list[str]:
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


def merge_to_chunks(runs: list[str], pauses: list[int], limit: int):
    """Greedily fill chunks up to `limit` chars, spanning run boundaries.

    Returns (chunk_texts, preserved, lost) where `preserved` counts the
    original pauses that landed on a chunk edge (kept as stitch-time
    silence) and `lost` counts the ones absorbed into a chunk's interior
    (the engine speaks straight through where a pause used to be).
    """
    chunks: list[str] = []
    current = ""
    preserved = 0
    lost = 0

    for index, run_text in enumerate(runs):
        candidate = f"{current} {run_text}".strip() if current else run_text
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.extend(_split(current, limit) if len(current) > limit else [current])
            current = run_text
            if index > 0:
                preserved += 1  # the boundary before this run is now a chunk edge
        if index < len(pauses):
            # This boundary (after `run_text`) either stays inside `current`
            # (lost, because the next run will be appended to the same chunk)
            # or will become an edge next iteration (counted above then).
            pass

    if current:
        chunks.extend(_split(current, limit) if len(current) > limit else [current])

    total_boundaries = len(pauses)
    lost = total_boundaries - preserved
    return chunks, preserved, max(lost, 0), total_boundaries


def main() -> int:
    document = extract(detect(str(SOURCE)))
    speech = head(normalize(document), TARGET_WORDS)
    runs, pauses = flatten(speech)
    total_chars = sum(len(r) for r in runs)
    print(f"{document.title!r}: {len(runs)} runs, {len(pauses)} pause boundaries, "
          f"{total_chars} chars\n")

    backend = KokoroBackend()
    backend._load()

    # Baseline: one synth call per run, exactly as milestone 4 does today.
    baseline_chunks = runs
    baseline_wall, baseline_audio = synth_all(backend, baseline_chunks)
    baseline_xrt = baseline_wall / baseline_audio

    print(f"{'chunk size':<12}{'#calls':>8}{'wall':>9}{'xRT':>7}{'speedup':>9}"
          f"{'pauses kept':>14}")
    print("-" * 60)
    print(f"{'(baseline)':<12}{len(baseline_chunks):>8}{baseline_wall:>8.1f}s"
          f"{baseline_xrt:>7.2f}{'1.00x':>9}"
          f"{len(pauses)}/{len(pauses)} (100%)".rjust(14))

    for size in CHUNK_SIZES:
        chunks, preserved, lost, total = merge_to_chunks(runs, pauses, size)
        wall, audio = synth_all(backend, chunks)
        xrt = wall / audio if audio else float("nan")
        speedup = baseline_wall / wall if wall else float("nan")
        kept_pct = 100 * preserved / total if total else 100.0
        print(f"{size:<12}{len(chunks):>8}{wall:>8.1f}s{xrt:>7.2f}{speedup:>8.2f}x"
              f"   {preserved}/{total} ({kept_pct:.0f}%)")

    return 0


def synth_all(backend: KokoroBackend, chunks: list[str]) -> tuple[float, float]:
    started = time.perf_counter()
    audio_seconds = 0.0
    for chunk in chunks:
        samples, rate = backend._load().create(chunk, voice=VOICE, speed=1.0, lang=LANG)
        audio_seconds += len(samples) / rate
    wall = time.perf_counter() - started
    return wall, audio_seconds


if __name__ == "__main__":
    sys.exit(main())
