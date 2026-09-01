import os

import pymupdf
import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test off the real ~/.config and ~/.cache, and off the network."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("NO_COLOR", "1")
    for name in [key for key in os.environ if key.startswith("READALOUD_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Fail loudly if a unit test tries to reach the internet.

    The live corpus run (scripts/corpus.py) is where real fetching is
    exercised; the test suite must stay offline and fast.
    """
    if "allow_network" in request.keywords:
        return

    def refuse(*args, **kwargs):
        raise AssertionError("a unit test tried to make a network request")

    import httpx

    monkeypatch.setattr(httpx.Client, "get", refuse)


ARTICLE_HTML = """<!DOCTYPE html>
<html><head><title>Site — The Tunnel Under the Thames</title>
<meta name="author" content="Jane Marlow"></head>
<body>
<nav><a href="/">Home</a> <a href="/news">News</a> <a href="/sport">Sport</a></nav>
<div class="cookie-banner">We use cookies to improve your experience. Accept all?</div>
<article>
  <h1>The Tunnel Under the Thames</h1>
  <p class="byline">By Jane Marlow</p>
  <p>The first tunnel beneath a navigable river opened in 1843, and for a while
     it was the most famous hole in the world. Londoners paid a penny to walk
     through it and buy souvenirs from stalls set into the arches.</p>
  <h2>How it was built</h2>
  <p>Marc Brunel patented a tunnelling shield after watching a shipworm bore
     through timber. The shield let miners excavate a small face at a time while
     the structure behind them was bricked up, which is essentially how tunnels
     are still dug today.</p>
  <ul>
    <li>The shield weighed some eighty tons in total</li>
    <li>Thirty-six miners could work the face at once</li>
  </ul>
  <blockquote>The Thames Tunnel is the greatest work of art in the world.</blockquote>
  <figure><img src="/tunnel.jpg">
    <figcaption>Figure 1: the tunnel shortly after opening.</figcaption></figure>
  <p>The tunnel flooded five times during construction and killed six men, and
     it took eighteen years to complete a stretch that a modern machine would
     manage in a fortnight.</p>
</article>
<aside class="related"><h3>Related articles</h3><a href="/1">Ten more tunnels</a></aside>
<div class="share">Share this on Facebook, Twitter, WhatsApp</div>
<footer>Copyright the newspaper. All rights reserved.</footer>
</body></html>
"""

PAYWALL_HTML = """<!DOCTYPE html>
<html><head><title>Markets wobble as bond yields climb</title>
<script type="application/ld+json">
{"@type":"NewsArticle","headline":"Markets wobble","isAccessibleForFree":false}
</script></head>
<body><article>
<h1>Markets wobble as bond yields climb</h1>
<div class="paywall-container"><p>Subscribe to continue reading.</p></div>
</article></body></html>
"""

# Gated markup, but a real teaser's worth of text. This must NOT raise: the
# teaser heuristic proved unreliable in both directions, so a short-but-real
# result is reported by word count instead.
TEASER_HTML = """<!DOCTYPE html>
<html><head><title>Markets wobble as bond yields climb</title>
<script type="application/ld+json">
{"@type":"NewsArticle","isAccessibleForFree":false}
</script></head>
<body><article>
<h1>Markets wobble as bond yields climb</h1>
<p>Investors spent Tuesday trying to work out whether the sell-off in long-dated
government debt was the start of something or merely a wobble, and by the close
almost nobody was willing to say which of the two it had been.</p>
<div class="paywall-container"><p>Subscribe to continue reading.</p></div>
</article></body></html>
"""

EMPTY_HTML = """<!DOCTYPE html>
<html><head><title>Video</title></head>
<body><nav>Home</nav><div id="player"></div><footer>Copyright</footer></body></html>
"""


def _lorem(prefix: str, sentences: int = 6) -> str:
    body = " ".join(
        f"{prefix} sentence {index} carries enough words to look like real "
        f"running text on the page."
        for index in range(1, sentences + 1)
    )
    return body


def make_pdf(path, two_column: bool = False, with_heading: bool = False,
             pages: int = 1, blank: bool = False):
    """Build a real PDF to extract from, so the tests exercise PyMuPDF itself."""
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=612, height=792)
        if blank:
            continue
        top = 60.0
        if with_heading:
            page.insert_textbox(
                pymupdf.Rect(50, 40, 562, 80), "A Study of Something", fontsize=20
            )
            top = 100.0
        if two_column:
            page.insert_textbox(
                pymupdf.Rect(50, top, 562, top + 40),
                f"Full Width Title Page {number}",
                fontsize=14,
            )
            page.insert_textbox(
                pymupdf.Rect(50, top + 60, 290, 700),
                _lorem(f"LEFT p{number}"),
                fontsize=10,
            )
            page.insert_textbox(
                pymupdf.Rect(322, top + 60, 562, 700),
                _lorem(f"RIGHT p{number}"),
                fontsize=10,
            )
            page.insert_textbox(
                pymupdf.Rect(50, 710, 290, 750),
                "Figure 1: a caption describing the figure above it.",
                fontsize=9,
            )
        else:
            page.insert_textbox(
                pymupdf.Rect(50, top, 562, 700), _lorem(f"BODY p{number}", 10), fontsize=10
            )
        page.insert_textbox(pymupdf.Rect(280, 760, 330, 780), str(number), fontsize=9)
    doc.save(str(path))
    doc.close()
    return path
