from pathlib import Path

from readaloud.document import Document
from readaloud.output import audio_path, default_stem, slugify, text_path


def test_slugify():
    assert slugify("The Tunnel Under the Thames") == "the-tunnel-under-the-thames"
    assert slugify("Café — naïve, résumé?") == "cafe-naive-resume"
    assert slugify("  spaces   and---dashes  ") == "spaces-and-dashes"
    assert slugify("!!!") == ""


def test_slugify_truncates_on_a_word_boundary():
    slug = slugify("word " * 40)
    assert len(slug) <= 80
    assert not slug.endswith("-")


def test_stem_prefers_the_title():
    document = Document(title="A Study of Something", url="https://x.example/p/12")
    assert default_stem(document) == "a-study-of-something"


def test_stem_falls_back_to_the_url():
    document = Document(url="https://x.example/posts/the-big-one")
    assert default_stem(document) == "the-big-one"


def test_stem_falls_back_to_the_path():
    document = Document(path=Path("/tmp/Quarterly Report.pdf"))
    assert default_stem(document) == "quarterly-report"


def test_stem_last_resort():
    assert default_stem(Document()) == "readaloud"


def test_derived_path_uses_the_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    document = Document(title="Hello There")
    assert audio_path(document, "m4a", None) == tmp_path / "hello-there.m4a"


def test_derived_path_does_not_clobber(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello-there.mp3").write_text("existing")
    document = Document(title="Hello There")
    assert audio_path(document, "mp3", None).name == "hello-there-2.mp3"


def test_explicit_out_is_taken_literally(tmp_path):
    document = Document(title="Hello There")
    target = tmp_path / "custom.mp3"
    target.write_text("existing")
    assert audio_path(document, "mp3", target) == target


def test_explicit_out_directory(tmp_path):
    document = Document(title="Hello There")
    assert audio_path(document, "mp3", tmp_path) == tmp_path / "hello-there.mp3"


def test_explicit_out_without_suffix_gets_one(tmp_path):
    document = Document(title="Hello")
    assert audio_path(document, "wav", tmp_path / "thing") == tmp_path / "thing.wav"


def test_text_path_sits_beside_the_audio():
    assert text_path(Path("/tmp/a/b.mp3")) == Path("/tmp/a/b.txt")
