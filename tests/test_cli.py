import re

import pytest
from conftest import ARTICLE_HTML, make_pdf

from readaloud import __version__, config
from readaloud.cli import main


@pytest.fixture
def article(tmp_path):
    path = tmp_path / "tunnel.html"
    path.write_text(ARTICLE_HTML, encoding="utf-8")
    return path


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_lists_the_flags(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--accent", "--gender", "--voice", "--engine", "--speed",
                 "--out", "--format", "--list-voices", "--preview",
                 "--estimate", "--keep-text", "--no-cache", "--verbose"):
        assert flag in out


def test_no_source_is_a_usage_error(capsys):
    assert main([]) == 2
    assert "no source given" in capsys.readouterr().err


def test_list_voices_rejects_a_source(capsys):
    assert main(["--list-voices", "https://example.com"]) == 2
    assert "does not take a source" in capsys.readouterr().err


def test_preview_and_estimate_conflict(capsys):
    assert main(["https://example.com", "--preview", "--estimate"]) == 2
    assert "cannot be used together" in capsys.readouterr().err


def test_out_of_range_speed_is_rejected_by_argparse(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["https://example.com", "--speed", "5"])
    assert exit_info.value.code == 2
    assert "speed must be between" in capsys.readouterr().err


def test_unknown_engine_is_rejected(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["https://example.com", "--engine", "wavenet"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_bad_source_reports_its_own_exit_code(capsys):
    assert main(["not a source at all"]) == 3
    assert "could not tell what" in capsys.readouterr().err


def test_verbose_shows_the_pipeline_stages(article, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main([str(article), "--verbose", "-a", "in", "-g", "male"])
    err = capsys.readouterr().err
    assert f"[source] html {article}" in err
    assert "[voice] in/male" in err
    assert "[engine] kokoro" in err
    assert "[extract]" in err
    assert "blocks" in err


def test_quiet_by_default(article, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([str(article)]) == 0
    captured = capsys.readouterr()
    assert "[source]" not in captured.err
    # On success stdout carries the path and the duration, and nothing else.
    assert re.fullmatch(r".*\.mp3\s+\d+:\d{2}", captured.out.strip())


def test_estimate_prints_counts_and_exits_zero(article, capsys):
    assert main([str(article), "--estimate"]) == 0
    out = capsys.readouterr().out
    assert "words" in out
    assert "characters" in out
    assert "runtime" in out


def test_estimate_respects_speed(article, capsys):
    main([str(article), "--estimate", "--speed", "2.0"])
    assert "at 2.0x" in capsys.readouterr().out


def test_keep_text_writes_a_file(article, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([str(article), "--keep-text"]) == 0
    written = tmp_path / "the-tunnel-under-the-thames.txt"
    assert "tunnelling shield" in written.read_text()
    assert ".mp3" in capsys.readouterr().out


def test_keep_text_honours_out(article, capsys, tmp_path):
    target = tmp_path / "audio" / "tunnel.mp3"
    assert main([str(article), "--keep-text", "-o", str(target)]) == 0
    assert (tmp_path / "audio" / "tunnel.txt").exists()


def test_pdf_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdf = make_pdf(tmp_path / "paper.pdf", two_column=True)
    assert main([str(pdf), "--keep-text"]) == 0
    # Named after the PDF's own heading, not the filename — see
    # _title_from_headings, which stopped BERT being announced as "bert".
    written = tmp_path / "full-width-title-page-1.txt"
    text = written.read_text()
    assert text.index("LEFT p1 sentence 1") < text.index("RIGHT p1 sentence 1")


def test_scanned_pdf_has_its_own_exit_code(tmp_path, capsys):
    pdf = make_pdf(tmp_path / "scan.pdf", blank=True, pages=3)
    assert main([str(pdf), "--estimate"]) == 7
    assert "no text layer" in capsys.readouterr().err


def test_audio_is_produced(article, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([str(article)]) == 0
    audio = tmp_path / "the-tunnel-under-the-thames.mp3"
    assert audio.exists() and audio.stat().st_size > 0


def test_each_paragraph_reaches_the_engine_separately(article, tmp_path,
                                                      monkeypatch, fake_backend):
    """The silence pause style splits at pauses so the gaps are real audio."""
    monkeypatch.chdir(tmp_path)
    main([str(article)])
    assert len(fake_backend.calls) > 3
    assert not any("[[slnc" in text for text, _, _ in fake_backend.calls)


def test_format_is_honoured(article, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main([str(article), "-f", "wav"])
    assert (tmp_path / "the-tunnel-under-the-thames.wav").exists()


def test_an_unavailable_accent_is_a_hard_error(article, capsys, tmp_path, monkeypatch):
    """A silent substitution costs a whole run to discover."""
    monkeypatch.chdir(tmp_path)
    assert main([str(article), "--accent", "au"]) == 12
    err = capsys.readouterr().err
    assert "no au voice" in err
    assert "--accent uk" in err
    assert not list(tmp_path.glob("*.mp3"))


def test_config_subcommand_prints_path_without_a_tty(capsys):
    assert main(["config"]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == str(config.config_path())
    assert config.config_path().exists()


def test_config_subcommand_takes_no_arguments(capsys):
    assert main(["config", "edit"]) == 2
    assert "takes no arguments" in capsys.readouterr().err


def test_list_voices_groups_by_accent(capsys):
    assert main(["--list-voices"]) == 0
    out = capsys.readouterr().out
    assert "uk" in out and "us" in out
    assert out.index("uk") < out.index("us")   # sorted


# --- milestone 3: word count, warnings, new flags, preview ----------------


def test_word_count_is_always_reported(article, capsys, tmp_path, monkeypatch):
    """Not gated on --verbose: you should never be told nothing before paying."""
    monkeypatch.chdir(tmp_path)
    main([str(article), "--keep-text"])
    assert "words extracted" in capsys.readouterr().err


def test_thin_url_gets_a_truncation_warning(capsys, monkeypatch, tmp_path):
    import httpx
    from conftest import TEASER_HTML

    class Fake:
        status_code = 200
        content = TEASER_HTML.encode()
        url = "https://example.com/markets"
        headers = {"content-type": "text/html; charset=utf-8"}

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, **kw: Fake())
    monkeypatch.chdir(tmp_path)
    main(["https://example.com/markets", "--keep-text"])
    err = capsys.readouterr().err
    assert "short for an article" in err
    assert "paywall markup" in err


def test_a_local_file_never_gets_the_url_warning(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    thin = tmp_path / "thin.html"
    thin.write_text("<html><body><article><p>" + "word " * 60 + "</p></article></body></html>")
    main([str(thin), "--keep-text"])
    assert "short for an article" not in capsys.readouterr().err


def test_header_is_on_by_default(article, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main([str(article), "--keep-text"])
    text = (tmp_path / "the-tunnel-under-the-thames.txt").read_text()
    assert text.startswith("The Tunnel Under the Thames.\n\nBy Jane Marlow.")


def test_no_header_drops_the_byline(article, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main([str(article), "--keep-text", "--no-header"])
    text = (tmp_path / "the-tunnel-under-the-thames.txt").read_text()
    assert "By Jane Marlow" not in text


def test_header_can_be_set_in_the_config_file(article, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[defaults]\nheader = false\n")
    main([str(article), "--keep-text"])
    assert "By Jane Marlow" not in (tmp_path / "the-tunnel-under-the-thames.txt").read_text()


def test_keep_captions_flag(article, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main([str(article), "--keep-text", "--keep-captions"])
    text = (tmp_path / "the-tunnel-under-the-thames.txt").read_text()
    assert "shortly after opening" in text


def test_keep_text_writes_the_spoken_text_not_the_raw_extraction(article, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main([str(article), "--keep-text"])
    text = (tmp_path / "the-tunnel-under-the-thames.txt").read_text()
    assert "# How it was built" not in text      # the old markdown rendering
    assert "How it was built." in text           # punctuated for the engine


def test_estimate_reports_spoken_and_extracted_counts(article, capsys):
    main([str(article), "--estimate"])
    out = capsys.readouterr().out
    assert "extracted" in out
    assert "words" in out
