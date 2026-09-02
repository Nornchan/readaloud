"""HTML → structured Document, via trafilatura.

trafilatura's XML output format is what we want: it keeps headings, lists,
quotes, tables and code as distinct elements instead of flattening everything
into one string. The plain-text output format throws that away.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import trafilatura
from lxml import etree, html as lxml_html

from readaloud.document import Block, BlockKind, Document
from readaloud.errors import ExtractionError, PaywallError

# Below this, a page is not an article — it's a stub, a consent wall, or a
# fetch that went wrong.
MIN_ARTICLE_WORDS = 25

# Between MIN_ARTICLE_WORDS and this, paywall markers are believable: enough
# text for a teaser paragraph, not enough for an article.
TEASER_WORDS = 100

# Substrings that show up in the markup of metered and gated articles.
_PAYWALL_MARKERS = (
    "paywall",
    "regwall",
    "meteredcontent",
    "metered-content",
    "subscription-required",
    "subscriber-only",
    "premium-content",
    "piano-",
    "tp-modal",
    "gate-container",
    "article-gate",
)

# schema.org's explicit "this is not free" signal, in its various spellings.
_NOT_FREE = re.compile(
    r'"isAccessibleForFree"\s*:\s*(?:false|"false"|"False")', re.IGNORECASE
)

_HEADING_LEVEL = re.compile(r"h(\d)")

# Separators sites commonly use before a trailing site name in <title>:
# "Article – Site", "Article | Site", "Article :: Site", "Article » Site".
# Deliberately excludes a bare hyphen-with-spaces from this list on its own
# risk profile -- see _strip_site_suffix, which only ever acts on the
# RIGHTMOST match and only when the trailing segment matches the page's own
# domain, so a real title that happens to contain one of these characters
# (the Guardian sample below) is left alone rather than mangled.
_TITLE_SEPARATOR = re.compile(r"\s[—–|»]\s|\s-\s|\s::\s")


def paywall_markers(raw_html: str) -> list[str]:
    """Which paywall signals, if any, this page carries."""
    found: list[str] = []
    if _NOT_FREE.search(raw_html):
        found.append("schema.org isAccessibleForFree=false")
    lowered = raw_html.lower()
    for marker in _PAYWALL_MARKERS:
        if marker in lowered:
            found.append(marker)
    if 'content_tier" content="locked' in lowered:
        found.append("article:content_tier=locked")
    return found


def prepare(raw_html: str) -> tuple[str, set[str]]:
    """Rewrite `<figcaption>` to `<p>`, and report the caption text.

    trafilatura discards figure captions outright. That sounds like what we
    want — the spec drops captions by default — but "dropped by the extractor"
    and "dropped by policy" are different things: the second is a rule the
    normalizer can be told to turn off, the first is unrecoverable. So we
    promote captions to paragraphs, remember their text, and label the
    resulting blocks CAPTION so the decision stays ours.

    Matching on exact caption text is far more precise than the "Figure 1:"
    prefix guess, which is all we have to go on in a PDF.
    """
    try:
        tree = lxml_html.fromstring(raw_html)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return raw_html, set()

    captions: set[str] = set()
    for element in list(tree.iter("figcaption")):
        text = _squash(" ".join(element.itertext()))
        if not text:
            continue
        captions.add(text)
        element.tag = "p"
        # The promoted paragraph is still inside <figure>, and trafilatura
        # discards that subtree wholesale, so unwrap the figure too. Only
        # figures that actually carry a caption are touched.
        parent = element.getparent()
        if parent is not None and parent.tag == "figure":
            parent.tag = "div"

    if not captions:
        return raw_html, set()
    return lxml_html.tostring(tree, encoding="unicode"), captions


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _element_text(element: etree._Element) -> str:
    """All text inside an element, including inline <hi> formatting children."""
    return _squash(" ".join(element.itertext()))


def _blocks_from_xml(root: etree._Element, captions: set[str]) -> list[Block]:
    body = root.find("main")
    if body is None:
        body = root
    blocks: list[Block] = []

    for element in body.iter():
        tag = element.tag
        if tag == "head":
            text = _element_text(element)
            if not text:
                continue
            match = _HEADING_LEVEL.search(element.get("rend", "") or "")
            level = int(match.group(1)) if match else 2
            blocks.append(Block(BlockKind.HEADING, text, level=level))
        elif tag == "p":
            text = _element_text(element)
            if not text:
                continue
            kind = BlockKind.CAPTION if text in captions else BlockKind.PARAGRAPH
            blocks.append(Block(kind, text))
        elif tag == "list":
            ordered = (element.get("rend") or "").startswith("ol")
            for index, item in enumerate(element.findall("item"), start=1):
                text = _element_text(item)
                if text:
                    blocks.append(
                        Block(
                            BlockKind.LIST_ITEM,
                            text,
                            level=1,
                            ordinal=index if ordered else None,
                        )
                    )
        elif tag == "quote":
            text = _element_text(element)
            if text:
                blocks.append(Block(BlockKind.QUOTE, text))
        elif tag == "table":
            cells = [_element_text(cell) for cell in element.iter("cell")]
            text = " | ".join(cell for cell in cells if cell)
            if text:
                blocks.append(Block(BlockKind.TABLE, text))
        elif tag == "code":
            text = element.text or _element_text(element)
            if text.strip():
                blocks.append(Block(BlockKind.CODE, text))

    return blocks


def _run_trafilatura(raw_html: str, url: str | None, favor_recall: bool) -> str | None:
    return trafilatura.extract(
        raw_html,
        url=url,
        output_format="xml",
        with_metadata=True,
        include_comments=False,
        include_tables=True,
        include_images=False,
        include_formatting=True,
        include_links=False,
        # Deliberately off: trafilatura's deduplication keeps an LRU of
        # paragraphs seen *across calls in the process*, so the recall retry
        # below would see the first pass's output as duplicate and discard the
        # whole article. We do our own (fuzzy) duplicate removal in the
        # normalizer, where pull quotes can be matched approximately.
        deduplicate=False,
        favor_recall=favor_recall,
    )


def extract_html(raw_html: str, url: str | None = None) -> Document:
    """Pull the article out of a page. Raises on paywalls and empty extractions."""
    prepared, captions = prepare(raw_html)

    xml = _run_trafilatura(prepared, url, favor_recall=False)
    blocks: list[Block] = []
    root = None

    if xml:
        root = etree.fromstring(xml.encode("utf-8"))
        blocks = _blocks_from_xml(root, captions)

    words = sum(block.word_count() for block in blocks)

    # A thin result is worth one more try with recall favoured — some sites
    # wrap the body in markup that the precision-first pass discards.
    if words < TEASER_WORDS:
        retry = _run_trafilatura(prepared, url, favor_recall=True)
        if retry:
            retry_root = etree.fromstring(retry.encode("utf-8"))
            retry_blocks = _blocks_from_xml(retry_root, captions)
            if sum(block.word_count() for block in retry_blocks) > words:
                root, blocks = retry_root, retry_blocks
                words = sum(block.word_count() for block in blocks)

    markers = paywall_markers(raw_html)
    where = url or "this page"

    # Only the unambiguous case raises here: essentially nothing extracted
    # *and* paywall markup present. The teaser band this used to cover turned
    # out to be unreliable in both directions — Business Insider and the New
    # Yorker both advertise isAccessibleForFree=false while serving the full
    # article — so short-but-not-empty results are now handled by the word
    # count the CLI prints on every run, which catches truncation from any
    # cause, not just from a paywall.
    if words < MIN_ARTICLE_WORDS and markers:
        raise PaywallError(
            f"{where} is gated — only {words} words of article text could be "
            f"extracted ({markers[0]})",
            "readaloud cannot sign in for you. Open the article in a browser "
            "where you are logged in, save it as HTML, and pass the file.",
        )

    if words < MIN_ARTICLE_WORDS:
        raise ExtractionError(
            f"no article text found in {where}",
            "The page may be a video, a listing page, or rendered entirely by "
            "JavaScript. Save it as HTML from your browser and pass the file.",
        )

    meta = root.attrib if root is not None else {}
    resolved_url = url or (meta.get("url") or None)
    title = meta.get("title") or None
    if title:
        title = _strip_site_suffix(title, resolved_url)
    return Document(
        blocks=blocks,
        title=title,
        author=meta.get("author") or None,
        date=meta.get("date") or None,
        url=resolved_url,
    )


def _strip_site_suffix(title: str, url: str | None) -> str:
    """"Article Title — Site Name" becomes "Article Title", so the spoken
    header says the article, not "Isambard Kingdom Brunel - Wikipedia."

    Conservative on purpose. A separator alone is not enough evidence: real
    titles contain dashes and colons as ordinary punctuation (a Guardian
    headline in the wild reads "...on her best friend – and love of her
    life", with no site suffix at all — stripping on separator shape would
    mangle it). The rightmost separator match is only accepted when its
    trailing segment plausibly names the site the page actually came
    from, checked against that URL's own domain — ground truth we already
    have, rather than a guess from the segment's length or shape.
    """
    if not title or not url:
        return title
    matches = list(_TITLE_SEPARATOR.finditer(title))
    if not matches:
        return title
    last = matches[-1]
    head, tail = title[: last.start()].strip(), title[last.end() :].strip()
    if not head or not tail:
        return title

    domain = urlparse(url).netloc.lower().removeprefix("www.")
    if not domain:
        return title
    labels = domain.split(".")
    domain_root = labels[-2] if len(labels) >= 2 else labels[0]
    domain_clean = re.sub(r"[^a-z0-9]", "", domain)
    tail_clean = re.sub(r"[^a-z0-9]", "", tail.lower())

    # Exact match only, deliberately. A substring/fuzzy fallback was tried
    # and rejected: "newyork" is a substring of "newyorker", so a title
    # like "Best Restaurants - New York" on newyorker.com would have had
    # its real content ("New York") mistaken for the site name and
    # deleted. Under-stripping a verbose site name is a mildly clunky
    # header; over-stripping is a title with real words missing, which is
    # worse, so this only ever fires on the two shapes actually observed
    # in the corpus: the bare domain ("peps.python.org") or its root label
    # ("wikipedia" from en.wikipedia.org).
    if tail_clean and (tail_clean == domain_clean or tail_clean == domain_root):
        return head
    return title
