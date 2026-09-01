#!/usr/bin/env python3
"""Render the same passage in several voices, for listening.

Kokoro's own grades put its British voices in the weaker half of its range,
and grades are not ears. This renders one real passage — pulled through the
whole pipeline, not a hand-written sample — so the difference can be heard.

    python scripts/compare_voices.py --out ./comparison
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from corpus import CORPUS  # noqa: E402

from readaloud.config import Settings  # noqa: E402
from readaloud.engines.kokoro import KokoroBackend  # noqa: E402
from readaloud.extract import extract  # noqa: E402
from readaloud.normalize import normalize  # noqa: E402
from readaloud.source import detect  # noqa: E402
from readaloud.speech import head, transcript, word_count  # noqa: E402
from readaloud.synthesize import duration, encode, format_duration, synthesize_once  # noqa: E402
from readaloud.terminal import Reporter  # noqa: E402

VOICES = ("bf_emma", "af_heart", "bm_fable")
WORDS = 500
DEFAULT_SOURCE = CORPUS / "html" / "wikipedia.html"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--out", default="comparison", type=Path)
    parser.add_argument("--words", type=int, default=WORDS)
    parser.add_argument("--voices", nargs="+", default=list(VOICES))
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    reporter = Reporter(verbose=True)

    document = extract(detect(args.source))
    passage = head(normalize(document), args.words)
    print(f"\n{word_count(passage)} words from {document.title!r}\n")

    (args.out / "passage.txt").write_text(transcript(passage), encoding="utf-8")

    backend = KokoroBackend()
    for voice in args.voices:
        target = args.out / f"{voice}.mp3"
        started = time.perf_counter()
        settings = Settings(engine="kokoro", voice=voice, speed=1.0, format="mp3")
        synthesize_once(passage, backend, settings, target, reporter)
        wall = time.perf_counter() - started
        length = duration(target)
        print(
            f"  {voice:<10} {format_duration(length)}  "
            f"({wall:.0f}s wall, {wall / length:.2f}x real time)  {target}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
