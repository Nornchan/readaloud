#!/usr/bin/env python3
"""Golden fixtures: the normalized speech IR for every corpus document.

The point is that a change to any normalization rule produces a *corpus-wide
diff*, so you can see what it did to sixteen real documents before deciding
whether you meant it.

    python scripts/golden.py --write    # regenerate (then read the git diff)
    python scripts/golden.py            # check, exit 1 on any difference
    python scripts/golden.py --diff bert

Requires the corpus: `python scripts/corpus.py --fetch` first. URL entries are
served from readaloud's own fetch cache, so they stay stable between runs.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from corpus import CORPUS, HTML_FILES, PDF_FILES, URLS  # noqa: E402

from readaloud.errors import ReadaloudError  # noqa: E402
from readaloud.extract import extract  # noqa: E402
from readaloud.normalize import NormalizeOptions, normalize  # noqa: E402
from readaloud.source import SourceKind, detect  # noqa: E402
from readaloud.speech import serialize, word_count  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"


def targets() -> list[tuple[str, str]]:
    """Every corpus entry, as (fixture name, source argument)."""
    entries = [(f"url-{name}", url) for name, (url, _) in URLS.items()]
    entries += [
        (f"html-{name}", str(CORPUS / "html" / f"{name}.html"))
        for name in HTML_FILES
    ]
    entries += [
        (f"pdf-{name}", str(CORPUS / "pdf" / f"{name}.pdf"))
        for name in PDF_FILES
    ]
    return entries


def render(target: str) -> str:
    """The fixture body for one source: the speech IR, or the error it raised."""
    try:
        source = detect(target)
        document = extract(source)
        speech = normalize(
            document, NormalizeOptions(is_pdf=source.kind is SourceKind.PDF)
        )
    except ReadaloudError as exc:
        return f"ERROR {type(exc).__name__}\n{exc.message}\n"

    header = (
        f"# {document.title or '(no title)'}\n"
        f"# {word_count(speech):,} spoken words, {len(speech)} nodes\n"
    )
    return header + serialize(speech)


def path_for(name: str) -> Path:
    return GOLDEN / f"{name}.speech"


def write_all() -> int:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, target in targets():
        if not target.startswith("http") and not Path(target).exists():
            print(f"  skip   {name} (not in the corpus)")
            continue
        body = render(target)
        path_for(name).write_text(body, encoding="utf-8")
        first = body.splitlines()[0]
        print(f"  wrote  {name:<18} {first[:60]}")
    return 0


def check(show: str | None = None) -> int:
    changed = []
    for name, target in targets():
        golden = path_for(name)
        if not golden.exists():
            print(f"  NEW    {name} — run --write")
            changed.append(name)
            continue
        if not target.startswith("http") and not Path(target).exists():
            continue
        expected = golden.read_text(encoding="utf-8")
        actual = render(target)
        if actual == expected:
            print(f"  same   {name}")
            continue
        changed.append(name)
        added = sum(
            1 for line in difflib.unified_diff(expected.splitlines(), actual.splitlines())
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = sum(
            1 for line in difflib.unified_diff(expected.splitlines(), actual.splitlines())
            if line.startswith("-") and not line.startswith("---")
        )
        print(f"  DIFF   {name:<18} +{added} -{removed}")
        if show in (name, "all"):
            for line in difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile=f"{name} (golden)", tofile=f"{name} (now)", lineterm="", n=1,
            ):
                print(f"    {line}")

    print(f"\n{len(changed)} of {len(targets())} fixtures changed")
    return 1 if changed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate the fixtures")
    parser.add_argument("--diff", metavar="NAME", help="show the diff for one fixture, or 'all'")
    args = parser.parse_args()
    return write_all() if args.write else check(args.diff)


if __name__ == "__main__":
    sys.exit(main())
