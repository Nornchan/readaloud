import pytest
from conftest import ARTICLE_HTML, EMPTY_HTML, PAYWALL_HTML, TEASER_HTML

from readaloud.document import BlockKind
from readaloud.errors import ExtractionError, PaywallError
from readaloud.extract.html import extract_html, paywall_markers


@pytest.fixture
def article():
    return extract_html(ARTICLE_HTML, url="https://example.com/tunnel")


def kinds(document):
    return [block.kind for block in document.blocks]


def texts(document, kind):
    return [block.text for block in document.blocks if block.kind is kind]


def test_metadata(article):
    assert "Tunnel Under the Thames" in article.title
    assert article.url == "https://example.com/tunnel"


def test_headings_keep_their_level(article):
    headings = [b for b in article.blocks if b.kind is BlockKind.HEADING]
    assert any(b.level == 1 and "Tunnel Under the Thames" in b.text for b in headings)
    assert any(b.level == 2 and b.text == "How it was built" for b in headings)


def test_paragraphs_survive(article):
    body = " ".join(texts(article, BlockKind.PARAGRAPH))
    assert "tunnelling shield" in body
    assert "flooded five times" in body


def test_list_items_are_their_own_blocks(article):
    items = texts(article, BlockKind.LIST_ITEM)
    assert "The shield weighed some eighty tons in total" in items
    assert "Thirty-six miners could work the face at once" in items


def test_quote_is_distinguished(article):
    assert any("greatest work of art" in text for text in texts(article, BlockKind.QUOTE))


def test_figcaption_becomes_a_caption_block(article):
    captions = texts(article, BlockKind.CAPTION)
    assert any("tunnel shortly after opening" in text for text in captions)
    # And is not also sitting in the body as a paragraph.
    assert not any("shortly after opening" in text
                   for text in texts(article, BlockKind.PARAGRAPH))


def test_boilerplate_is_gone(article):
    everything = " ".join(block.text for block in article.blocks).lower()
    for junk in ("cookies", "share this", "related articles", "all rights reserved",
                 "home news sport"):
        assert junk not in everything


def test_structure_is_not_flattened(article):
    assert BlockKind.HEADING in kinds(article)
    assert BlockKind.LIST_ITEM in kinds(article)
    assert len(article.blocks) > 5


def test_word_and_char_counts(article):
    assert article.word_count() > 80
    assert article.char_count() > article.word_count()


def test_gated_page_with_no_text_raises_its_own_error():
    with pytest.raises(PaywallError) as caught:
        extract_html(PAYWALL_HTML, url="https://example.com/markets")
    assert "gated" in caught.value.message
    assert "isAccessibleForFree" in caught.value.message
    assert "save it as html" in caught.value.hint.lower()


def test_a_real_teaser_is_not_treated_as_an_error():
    """Deprioritised on purpose — the CLI's word count covers this instead."""
    document = extract_html(TEASER_HTML, url="https://example.com/markets")
    assert 25 < document.word_count() < 400


def test_paywall_error_is_not_the_empty_page_error():
    with pytest.raises(ExtractionError) as caught:
        extract_html(EMPTY_HTML, url="https://example.com/video")
    assert not isinstance(caught.value, PaywallError)
    assert "no article text" in caught.value.message


def test_paywall_markers_are_reported():
    assert paywall_markers(PAYWALL_HTML)
    assert not paywall_markers(ARTICLE_HTML)


def test_paywall_markup_on_a_full_article_is_ignored():
    # Plenty of sites ship paywall markup on pages that are not actually gated.
    hybrid = ARTICLE_HTML.replace("<article>", '<article><div class="paywall"></div>')
    document = extract_html(hybrid, url="https://example.com/tunnel")
    assert document.word_count() > 80


def test_plain_text_rendering_is_readable(article):
    text = article.plain_text()
    assert "# How it was built" in text
    assert "- The shield weighed" in text
    assert text.endswith("\n")


# --- title site-suffix stripping -------------------------------------------

from readaloud.extract.html import _strip_site_suffix


def test_strips_a_pipe_separated_domain_suffix():
    assert _strip_site_suffix(
        "PEP 8 – Style Guide for Python Code | peps.python.org",
        "https://peps.python.org/pep-0008/",
    ) == "PEP 8 – Style Guide for Python Code"


def test_strips_a_hyphen_separated_site_name():
    assert _strip_site_suffix(
        "Isambard Kingdom Brunel - Wikipedia", "https://en.wikipedia.org/wiki/Brunel"
    ) == "Isambard Kingdom Brunel"


def test_a_real_en_dash_in_the_title_is_not_mistaken_for_a_suffix():
    """The trap case: real title content containing the separator character,
    with no site suffix at all."""
    title = (
        "The extraordinary untold story of Freddie Mercury: Mary Austin on "
        "her best friend – and love of her life"
    )
    assert _strip_site_suffix(title, "https://www.theguardian.com/music/x") == title


def test_a_short_trailing_segment_that_merely_resembles_the_domain_survives():
    """newyork is a substring of newyorker -- a fuzzy match would eat real
    title content here. Exact match only."""
    title = "Best Restaurants - New York"
    assert _strip_site_suffix(title, "https://www.newyorker.com/food/best") == title


def test_no_url_means_no_strip():
    assert _strip_site_suffix("Some Title - Site", None) == "Some Title - Site"


def test_no_separator_is_untouched():
    assert _strip_site_suffix("A Title With No Suffix", "https://example.com/a") == \
        "A Title With No Suffix"


def test_verbose_site_name_that_does_not_match_the_domain_is_left_alone():
    """Under-stripping (a slightly clunky header) is the accepted cost of
    never over-stripping real content."""
    title = "Q3 Earnings Beat Estimates - The Widget Gazette"
    assert _strip_site_suffix(title, "https://widgetgazette.example.com/x") == title


def test_end_to_end_through_extract_html():
    document = extract_html(ARTICLE_HTML, url="https://example.com/tunnel")
    # ARTICLE_HTML's own title has no suffix; this just confirms the pipeline
    # calls the strip without breaking an ordinary title.
    assert document.title == "The Tunnel Under the Thames"
