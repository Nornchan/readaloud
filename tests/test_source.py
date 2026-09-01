import pytest

from readaloud.errors import SourceError
from readaloud.source import SourceKind, detect

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
MINIMAL_HTML = b"<!DOCTYPE html>\n<html><body><p>Hi</p></body></html>\n"


def test_https_url():
    source = detect("https://example.com/article")
    assert source.kind is SourceKind.URL
    assert source.url == "https://example.com/article"
    assert source.path is None


def test_http_url_is_not_upgraded_silently():
    assert detect("http://example.com/a").url == "http://example.com/a"


def test_bare_domain_becomes_https():
    source = detect("example.com/article")
    assert source.kind is SourceKind.URL
    assert source.url == "https://example.com/article"


def test_bare_domain_loses_to_a_real_file(tmp_path, monkeypatch):
    local = tmp_path / "example.com"
    local.write_bytes(MINIMAL_HTML)
    monkeypatch.chdir(tmp_path)
    assert detect("example.com").kind is SourceKind.HTML


def test_url_without_host_is_rejected():
    with pytest.raises(SourceError, match="not a complete URL"):
        detect("https://")


def test_unsupported_scheme():
    with pytest.raises(SourceError, match="unsupported URL scheme"):
        detect("ftp://example.com/a.pdf")


def test_pdf_by_magic_bytes_regardless_of_extension(tmp_path):
    path = tmp_path / "actually-a.pdf.txt"
    path.write_bytes(MINIMAL_PDF)
    source = detect(str(path))
    assert source.kind is SourceKind.PDF
    assert source.path == path


def test_html_by_magic_bytes(tmp_path):
    path = tmp_path / "page.html"
    path.write_bytes(MINIMAL_HTML)
    assert detect(str(path)).kind is SourceKind.HTML


def test_html_fragment_by_extension(tmp_path):
    path = tmp_path / "fragment.htm"
    path.write_bytes(b"<p>no wrapper element at all</p>")
    assert detect(str(path)).kind is SourceKind.HTML


def test_pdf_extension_with_wrong_content_is_an_error(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"Not a PDF at all, just some words.\n")
    with pytest.raises(SourceError, match="not a PDF file"):
        detect(str(path))


def test_unknown_extension_is_an_error(tmp_path):
    path = tmp_path / "notes.docx"
    path.write_bytes(b"PK\x03\x04binary")
    with pytest.raises(SourceError, match="unsupported file type"):
        detect(str(path))


def test_file_url(tmp_path):
    path = tmp_path / "page.html"
    path.write_bytes(MINIMAL_HTML)
    assert detect(path.as_uri()).kind is SourceKind.HTML


def test_missing_file(tmp_path):
    with pytest.raises(SourceError, match="could not tell what"):
        detect(str(tmp_path / "nope.pdf"))


def test_directory_is_rejected(tmp_path):
    with pytest.raises(SourceError, match="is a directory"):
        detect(str(tmp_path))


def test_empty_argument():
    with pytest.raises(SourceError, match="empty source"):
        detect("   ")


def test_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "doc.pdf").write_bytes(MINIMAL_PDF)
    assert detect("~/doc.pdf").kind is SourceKind.PDF
