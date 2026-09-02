#!/usr/bin/env python3
"""Calibrate the truncation guard's chars/s constant against the real corpus.

The guard in engines/kokoro.py rejects audio that's implausibly short for its
character count. The constant behind that check was set from three voices on
one document. This samples real chunks across all 16 corpus documents (HTML
and PDF, local files and cached fetches) with one fixed voice, so the
docstring's claim about the threshold's safety margin is backed by measured
data rather than a guess extrapolated from one passage.

    python scripts/calibrate_truncation.py [--chunks-per-doc N] [--chunk-chars N]
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from corpus import CORPUS, HTML_FILES, PDF_FILES, URLS  # noqa: E402

from readaloud.engines.kokoro import SAMPLE_RATE, KokoroBackend  # noqa: E402
from readaloud.errors import ReadaloudError  # noqa: E402
from readaloud.extract import extract  # noqa: E402
from readaloud.normalize import normalize  # noqa: E402
from readaloud.source import SourceKind, detect  # noqa: E402
from readaloud.speech import Pause, Say, Spell  # noqa: E402

VOICE = "bf_emma"
LANG = "en-gb"


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


def chunks_for(target: str, chunk_chars: int) -> list[str]:
    source = detect(target)
    document = extract(source)
    speech = normalize(document)
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
        chunks.extend(_split(run_text, chunk_chars))
    return [c for c in chunks if len(c) >= 60]


def sample(chunks: list[str], n: int) -> list[str]:
    """Spread the sample across the document rather than take the head."""
    if len(chunks) <= n:
        return chunks
    step = len(chunks) / n
    return [chunks[int(i * step)] for i in range(n)]


def targets() -> list[tuple[str, str, str]]:
    """(name, kind, source-argument) for every corpus entry with real text."""
    out = []
    for name, (url, expect) in URLS.items():
        if expect == "ok":
            out.append((f"url-{name}", "html", url))
    for name in HTML_FILES:
        out.append((f"html-{name}", "html", str(CORPUS / "html" / f"{name}.html")))
    for name, (_, expect) in PDF_FILES.items():
        if expect == "ok":
            out.append((f"pdf-{name}", "pdf", str(CORPUS / "pdf" / f"{name}.pdf")))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks-per-doc", type=int, default=2)
    parser.add_argument("--chunk-chars", type=int, default=450)
    args = parser.parse_args()

    backend = KokoroBackend()
    backend._load()

    rows = []  # (doc, kind, chars, seconds, chars_per_sec)
    print(f"{'document':<16}{'kind':<6}{'chars':>7}{'sec':>8}{'chars/s':>10}")
    print("-" * 47)

    for name, kind, target in targets():
        try:
            chunks = sample(chunks_for(target, args.chunk_chars), args.chunks_per_doc)
        except ReadaloudError as exc:
            print(f"{name:<16}{kind:<6}  SKIP ({type(exc).__name__})")
            continue
        for chunk in chunks:
            started = time.perf_counter()
            samples, rate = backend._load().create(chunk, voice=VOICE, speed=1.0, lang=LANG)
            wall = time.perf_counter() - started
            seconds = len(samples) / rate
            if seconds <= 0:
                print(f"{name:<16}{kind:<6}  EMPTY OUTPUT for chunk of {len(chunk)} chars")
                continue
            rate_cps = len(chunk) / seconds
            rows.append((name, kind, len(chunk), seconds, rate_cps))
            print(f"{name:<16}{kind:<6}{len(chunk):>7}{seconds:>8.1f}{rate_cps:>10.2f}"
                  f"   (call took {wall:.1f}s)")

    if not rows:
        print("no data")
        return 1

    all_rates = [r[4] for r in rows]
    html_rates = [r[4] for r in rows if r[1] == "html"]
    pdf_rates = [r[4] for r in rows if r[1] == "pdf"]

    def stats(label: str, values: list[float]) -> None:
        if not values:
            print(f"{label}: no data")
            return
        print(f"{label:<8} n={len(values):<3} min={min(values):5.2f}  "
              f"median={median(values):5.2f}  mean={mean(values):5.2f}  "
              f"max={max(values):5.2f}")

    print("\n=== chars/s distribution ===")
    stats("all", all_rates)
    stats("html", html_rates)
    stats("pdf", pdf_rates)

    calibrated_mean = mean(all_rates)

    # The runtime guard fires when observed_seconds < expected_seconds *
    # TRUNCATION_RATIO, where expected_seconds is derived from the mean
    # rate. In rate terms that means it fires when observed_rate exceeds
    # CHARS_PER_SECOND / TRUNCATION_RATIO -- so the binding constraint is
    # the FASTEST legitimate rate observed (least audio for its length),
    # not the slowest. The slowest samples (PDF table remnants read digit
    # by digit) are real, correct, very slow speech and are irrelevant to
    # this threshold; including them as "the floor" would calibrate the
    # guard against the wrong tail entirely.
    ratios = sorted(
        (calibrated_mean / rate, name, chars) for name, _, chars, _, rate in rows
    )
    tightest_ratio, tightest_doc, tightest_chars = ratios[0]

    print(f"\ncalibrated CHARS_PER_SECOND (mean): {calibrated_mean:.2f}")
    print(f"tightest (fastest legitimate) ratio: {tightest_ratio:.3f}  "
          f"({tightest_doc}, {tightest_chars} chars)")
    print("this is the number TRUNCATION_RATIO must stay safely below:")
    for candidate in (0.35, 0.45, 0.50, 0.55, 0.60, 0.65):
        margin = (tightest_ratio - candidate) / tightest_ratio
        flag = "  UNSAFE — at or above the tightest observed case" if candidate >= tightest_ratio else ""
        print(f"  TRUNCATION_RATIO={candidate}: {margin:.0%} margin below tightest observed{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
