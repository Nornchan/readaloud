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
