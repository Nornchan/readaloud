#!/usr/bin/env python3
"""Run extraction against real articles and report what came out.

The unit tests are offline and synthetic; this is the counterpart that points
the extractor at the actual web, where markup is hostile and PDFs are strange.

    python scripts/corpus.py --fetch    # download the local HTML/PDF corpus
    python scripts/corpus.py            # extract everything and print a report
    python scripts/corpus.py --show url:bbc   # dump one document's text

Article URLs rot. When one 404s, replace it — the point is five real examples
of each kind, not these five specifically.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pymupdf  # noqa: E402

from readaloud.config import cache_home  # noqa: E402
from readaloud.document import BlockKind  # noqa: E402
from readaloud.errors import ReadaloudError  # noqa: E402
from readaloud.extract import extract  # noqa: E402
from readaloud.fetch import fetch  # noqa: E402
from readaloud.source import detect  # noqa: E402

CORPUS = cache_home() / "corpus"

# --- live URLs -------------------------------------------------------------

URLS = {
    "bbc": ("https://www.bbc.com/news/articles/c24j5192j7jo", "ok"),
    "guardian": (
        "https://www.theguardian.com/music/2026/sep/01/"
        "freddie-mercury-lover-mary-austin-interview",
        "ok",
    ),
    "ars": (
        "https://arstechnica.com/gaming/2026/08/"
        "pockets-ai-made-my-game-ideas-real-now-meta-controls-the-results/",
        "ok",
    ),
    "wikipedia": ("https://en.wikipedia.org/wiki/Thames_Tunnel", "ok"),
    # A long magazine feature. Worth keeping even though it turned out not to
    # be gated for us: most publishers serve full text to a browser UA, which
    # is itself the finding — see `subscription` below.
    "atlantic": (
        "https://www.theatlantic.com/magazine/archive/2024/06/"
        "daniel-radcliffe-merrily-we-roll-along-jk-rowling/678219/",
        "ok",
    ),
    # A real paywall, gated at the HTTP layer rather than by serving a teaser.
    "telegraph": ("https://www.telegraph.co.uk/", "subscription"),
}

# --- pages saved to disk and read as local .html files ---------------------

HTML_FILES = {
    "pep8": ("https://peps.python.org/pep-0008/", "ok"),
    "wikipedia": ("https://en.wikipedia.org/wiki/Brunel", "ok"),
    "mdn": (
        "https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/figcaption",
        "ok",
    ),
    "guardian": (
        "https://www.theguardian.com/music/2026/aug/31/"
        "tupac-shakur-killing-duane-keith-davis",
        "ok",
    ),
    # Written by hand: every kind of boilerplate the spec says to strip.
    "boilerplate": (None, "ok"),
}

# --- PDFs ------------------------------------------------------------------

PDF_FILES = {
    "bert": ("https://arxiv.org/pdf/1810.04805", "ok"),          # two-column ACL
    "transformer": ("https://arxiv.org/pdf/1706.03762", "ok"),   # single column
    "gpt3": ("https://arxiv.org/pdf/2005.14165", "ok"),          # long, headers/footers
    "irs1040": ("https://www.irs.gov/pub/irs-pdf/f1040.pdf", "ok"),  # a form, not prose
    # Built by rasterising a real PDF: a scan with no text layer.
    "scan": (None, "no-text-layer"),
}

BOILERPLATE_HTML = """<!DOCTYPE html>
<html><head><title>Everything We Strip — The Daily Boilerplate</title></head>
<body>
<div id="cookie-consent">This site uses cookies. Accept all cookies?</div>
<nav><ul><li><a href="/">Home</a></li><li><a href="/uk">UK</a></li>
<li><a href="/world">World</a></li><li><a href="/sport">Sport</a></li></ul></nav>
<header><a href="/subscribe">Subscribe now for unlimited access</a></header>
<main><article>
<h1>The Great Eastern Was a Bad Idea</h1>
<p class="standfirst">A ship so large it took three months to launch sideways.</p>
<p>Isambard Kingdom Brunel's last ship was six times larger by displacement than
any vessel afloat when she was laid down, and she nearly ruined everyone who
touched her. The hull was double-skinned, which was a genuine innovation, and
she was designed to steam to Australia without refuelling, which no ship could
then do.</p>
<h2>The launch</h2>
<p>Because the Thames was too narrow to launch her stern-first, she had to go in
sideways, down greased ways, pulled by hydraulic rams. The first attempt killed
a man and moved the ship three feet. It took until the end of January to get her
into the water, by which point the launch had consumed most of the money set
aside for fitting her out.</p>
<figure><img src="/ship.jpg"><figcaption>Figure 2: the ship on the stocks, 1857.</figcaption></figure>
<p>She never carried passengers to Australia. She spent her working life laying
telegraph cable across the Atlantic, which is the one thing she turned out to be
extraordinarily good at, and ended as a floating advertising hoarding in the
Mersey before being broken up.</p>
<div class="share-tools">Share this article: Facebook Twitter WhatsApp Email</div>
<aside class="author-bio"><h3>About the author</h3>
<p>Jane Marlow is a maritime historian and the author of four books. She lives in
Bristol with two cats and writes a weekly newsletter about Victorian engineering
disasters, which you can subscribe to at the link below.</p></aside>
</article></main>
<section class="related-articles"><h3>Related articles</h3>
<ul><li><a href="/1">Ten ships that bankrupted their owners</a></li>
<li><a href="/2">The bridge that fell down twice</a></li></ul></section>
<section id="comments"><h3>Comments (412)</h3>
<div class="comment"><p>My great-grandfather worked on that ship!</p></div>
<div class="comment"><p>Actually the tonnage figure quoted here is wrong.</p></div></section>
<footer><p>Copyright 2026 The Daily Boilerplate. All rights reserved.
Terms of service. Privacy policy. Do not sell my personal information.</p></footer>
</body></html>
"""


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    response = fetch(url)
    path.write_bytes(response.body)


def build_corpus() -> None:
    """Populate the local corpus: download pages and PDFs, build the scan."""
    for name, (url, _) in HTML_FILES.items():
        path = CORPUS / "html" / f"{name}.html"
        if name == "boilerplate":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(BOILERPLATE_HTML, encoding="utf-8")
            print(f"  wrote    html/{name}.html")
            continue
        if path.exists():
            print(f"  have     html/{name}.html")
            continue
        try:
            download(url, path)
            print(f"  fetched  html/{name}.html")
        except ReadaloudError as exc:
            print(f"  FAILED   html/{name}.html — {exc.message}")

    for name, (url, _) in PDF_FILES.items():
        path = CORPUS / "pdf" / f"{name}.pdf"
        if name == "scan":
            continue
        if path.exists():
            print(f"  have     pdf/{name}.pdf")
            continue
        try:
            download(url, path)
            print(f"  fetched  pdf/{name}.pdf")
        except ReadaloudError as exc:
            print(f"  FAILED   pdf/{name}.pdf — {exc.message}")

    # A real scan: render pages to images and rebuild them as an image-only PDF.
    scan = CORPUS / "pdf" / "scan.pdf"
    source = CORPUS / "pdf" / "transformer.pdf"
    if not scan.exists() and source.exists():
        original = pymupdf.open(source)
        rasterised = pymupdf.open()
        for page in list(original)[:3]:
            pixmap = page.get_pixmap(dpi=110)
            new_page = rasterised.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, pixmap=pixmap)
        rasterised.save(scan)
        rasterised.close()
        original.close()
        print("  built    pdf/scan.pdf (rasterised, no text layer)")


def check(name: str, target: str, expectation: str) -> dict:
    started = time.perf_counter()
    row = {"name": name, "expect": expectation, "target": target}
    try:
        document = extract(detect(target))
    except ReadaloudError as exc:
        row["outcome"] = type(exc).__name__
        row["detail"] = exc.message
        name_of = type(exc).__name__
        row["ok"] = (
            (expectation == "paywall" and name_of == "PaywallError")
            or (expectation == "no-text-layer" and name_of == "NoTextLayerError")
            or (
                expectation == "subscription"
                and name_of == "FetchError"
                and "subscription or login" in exc.message
            )
        )
    else:
        kinds = {kind: 0 for kind in BlockKind}
        for block in document.blocks:
            kinds[block.kind] += 1
        row["outcome"] = "extracted"
        row["words"] = document.word_count()
        row["blocks"] = len(document.blocks)
        row["title"] = (document.title or "—")[:52]
        row["kinds"] = " ".join(
            f"{kind.value[:4]}:{count}" for kind, count in kinds.items() if count
        )
        row["ok"] = expectation == "ok" and document.word_count() > 150
        row["document"] = document
    row["seconds"] = time.perf_counter() - started
    return row


def report(rows: list[dict]) -> bool:
    everything_ok = True
    for row in rows:
        mark = "PASS" if row["ok"] else "FAIL"
        if not row["ok"]:
            everything_ok = False
        head = f"  [{mark}] {row['name']:<22} {row['seconds']:>5.2f}s  "
        if row["outcome"] == "extracted":
            print(head + f"{row['words']:>6,} words  {row['blocks']:>4} blocks  {row['kinds']}")
            print(f"{'':<33}title: {row['title']}")
        else:
            print(head + f"{row['outcome']}")
            print(f"{'':<33}{row['detail'][:96]}")
    return everything_ok


def reading_order_check(rows: list[dict]) -> None:
    """The two-column paper must not come out interleaved."""
    for row in rows:
        if row["name"] != "pdf:bert" or "document" not in row:
            return
        document = row["document"]
        page_one = [b for b in document.blocks if b.page == 1]
        text = "\n".join(b.text for b in page_one)
        print("\nTwo-column reading order (BERT, page 1, first 6 blocks):")
        for block in page_one[:6]:
            preview = " ".join(block.text.split())[:88]
            print(f"  {block.kind.value:<9} pos={block.position:.2f}  {preview}")
        marker = "Abstract"
        if marker in text:
            print(f"  → 'Abstract' appears at character {text.index(marker)} of page 1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="build the local corpus")
    parser.add_argument("--show", metavar="NAME", help="dump one document's text")
    parser.add_argument("--skip-urls", action="store_true", help="local files only")
    args = parser.parse_args()

    if args.fetch:
        print("Building corpus in", CORPUS)
        build_corpus()
        return 0

    rows: list[dict] = []

    if not args.skip_urls:
        print("\nURLs (fetched live)")
        url_rows = [check(f"url:{n}", u, e) for n, (u, e) in URLS.items()]
        report(url_rows)
        rows += url_rows

    print("\nLocal HTML files")
    html_rows = [
        check(f"html:{n}", str(CORPUS / "html" / f"{n}.html"), e)
        for n, (_, e) in HTML_FILES.items()
        if (CORPUS / "html" / f"{n}.html").exists()
    ]
    report(html_rows)
    rows += html_rows

    print("\nPDFs")
    pdf_rows = [
        check(f"pdf:{n}", str(CORPUS / "pdf" / f"{n}.pdf"), e)
        for n, (_, e) in PDF_FILES.items()
        if (CORPUS / "pdf" / f"{n}.pdf").exists()
    ]
    report(pdf_rows)
    rows += pdf_rows

    reading_order_check(pdf_rows)

    if args.show:
        for row in rows:
            if row["name"] == args.show and "document" in row:
                print("\n" + "=" * 70)
                print(row["document"].plain_text())

    failures = [row["name"] for row in rows if not row["ok"]]
    print(f"\n{len(rows) - len(failures)}/{len(rows)} as expected")
    if failures:
        print("unexpected:", ", ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
