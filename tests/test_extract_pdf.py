import pytest
from conftest import make_pdf

from readaloud.document import BlockKind
from readaloud.errors import ExtractionError, NoTextLayerError
from readaloud.extract.pdf import extract_pdf


def read(path):
    return extract_pdf(path.read_bytes(), path_label=str(path))


def body_text(document):
    return "\n".join(block.text for block in document.blocks)


def test_single_column(tmp_path):
    document = read(make_pdf(tmp_path / "plain.pdf"))
    assert document.page_count == 1
    assert "BODY p1 sentence 1" in body_text(document)
    assert document.word_count() > 50


def test_two_column_reading_order(tmp_path):
    document = read(make_pdf(tmp_path / "paper.pdf", two_column=True))
    text = body_text(document)
    first_left = text.index("LEFT p1 sentence 1")
    last_left = text.index("LEFT p1 sentence 6")
    first_right = text.index("RIGHT p1 sentence 1")
    # The whole left column comes before any of the right column — not
    # interleaved line by line, which is what a naive top-to-bottom sort gives.
    assert first_left < last_left < first_right


def test_full_width_block_precedes_both_columns(tmp_path):
    document = read(make_pdf(tmp_path / "paper.pdf", two_column=True))
    text = body_text(document)
    assert text.index("Full Width Title") < text.index("LEFT p1")


def test_two_column_order_holds_across_pages(tmp_path):
    document = read(make_pdf(tmp_path / "paper.pdf", two_column=True, pages=3))
    text = body_text(document)
    positions = [text.index(f"LEFT p{n} sentence 1") for n in (1, 2, 3)]
    assert positions == sorted(positions)
    assert text.index("RIGHT p1 sentence 1") < text.index("LEFT p2 sentence 1")


def test_pages_are_recorded(tmp_path):
    document = read(make_pdf(tmp_path / "paper.pdf", pages=3))
    assert document.page_count == 3
    assert {block.page for block in document.blocks} == {1, 2, 3}


def test_vertical_position_is_recorded(tmp_path):
    """Header/footer removal in the normalizer keys off this."""
    document = read(make_pdf(tmp_path / "paper.pdf", pages=2))
    page_numbers = [b for b in document.blocks if b.text.strip() in ("1", "2")]
    assert page_numbers, "the standalone page numbers should be extracted"
    assert all(block.position > 0.9 for block in page_numbers)
    body = [b for b in document.blocks if "BODY" in b.text]
    assert all(block.position < 0.9 for block in body)


def test_heading_detected_by_font_size(tmp_path):
    document = read(make_pdf(tmp_path / "study.pdf", with_heading=True))
    headings = [b for b in document.blocks if b.kind is BlockKind.HEADING]
    assert any("A Study of Something" in block.text for block in headings)


def test_figure_caption_detected(tmp_path):
    document = read(make_pdf(tmp_path / "paper.pdf", two_column=True))
    captions = [b for b in document.blocks if b.kind is BlockKind.CAPTION]
    assert any(block.text.startswith("Figure 1:") for block in captions)


def test_line_breaks_are_preserved_for_the_normalizer(tmp_path):
    """Hard-wrap repair needs to see where the lines actually broke."""
    document = read(make_pdf(tmp_path / "plain.pdf"))
    assert any("\n" in block.text for block in document.blocks)


def test_scanned_pdf_raises_no_text_layer(tmp_path):
    with pytest.raises(NoTextLayerError) as caught:
        read(make_pdf(tmp_path / "scan.pdf", blank=True, pages=4))
    assert "no text layer" in caught.value.message
    assert "ocr" in caught.value.hint.lower()


def test_no_text_layer_is_not_a_generic_extraction_error(tmp_path):
    with pytest.raises(NoTextLayerError) as caught:
        read(make_pdf(tmp_path / "scan.pdf", blank=True))
    assert caught.value.exit_code != ExtractionError.exit_code


def test_corrupt_pdf(tmp_path):
    with pytest.raises(ExtractionError, match="could not open"):
        extract_pdf(b"%PDF-1.4 this is not really a pdf at all", path_label="broken.pdf")
