"""The chunker, against SPEC.md Appendix A.3's contract."""

import pytest

from readaloud.chunk import Chunk, chunk_run, chunk_speech
from readaloud.speech import Pause, Say, Spell, render
from readaloud.terminal import Reporter


def said(chunk: Chunk) -> str:
    return render(list(chunk.nodes), "silence")


# --- rule 1: split at Pause boundaries first -------------------------------


def test_a_run_that_fits_becomes_one_chunk():
    speech = [Say("A short paragraph."), Pause(500)]
    chunks = chunk_speech(speech, max_chars=2000, pause_style="silence")
    assert len(chunks) == 1
    assert said(chunks[0]) == "A short paragraph."
    assert chunks[0].pause_after_ms == 500


def test_each_run_is_its_own_chunk_regardless_of_size():
    """Two small runs never merge into one call, however much headroom
    max_chars leaves — the curve measurement showed merging is both slower
    and pause-lossy, so a Pause is always a hard chunk boundary."""
    speech = [
        Say("First paragraph."), Pause(500),
        Say("Second paragraph."), Pause(500),
    ]
    chunks = chunk_speech(speech, max_chars=2000, pause_style="silence")
    assert len(chunks) == 2
    assert said(chunks[0]) == "First paragraph."
    assert said(chunks[1]) == "Second paragraph."
    assert chunks[0].pause_after_ms == 500
    assert chunks[1].pause_after_ms == 500


def test_the_last_run_with_no_trailing_pause_gets_zero():
    speech = [Say("Only paragraph.")]
    chunks = chunk_speech(speech, max_chars=2000, pause_style="silence")
    assert chunks[-1].pause_after_ms == 0


def test_spell_nodes_survive_run_grouping():
    speech = [Say("Funded by the"), Spell("US"), Say("government."), Pause(500)]
    chunks = chunk_speech(speech, max_chars=2000, pause_style="silence")
    assert len(chunks) == 1
    assert said(chunks[0]) == "Funded by the U. S. government."


def test_empty_speech_returns_no_chunks():
    assert chunk_speech([], max_chars=2000, pause_style="silence") == []


def test_a_pause_with_nothing_spoken_before_it_is_dropped_defensively():
    """normalize()'s _tidy() should never produce this, but the chunker must
    not crash if it somehow does."""
    speech = [Pause(500), Say("Body."), Pause(500)]
    chunks = chunk_speech(speech, max_chars=2000, pause_style="silence")
    assert len(chunks) == 1
    assert said(chunks[0]) == "Body."


# --- packing multiple sentences into an oversized run's chunks ------------


def test_a_run_under_the_limit_is_never_split():
    nodes = (Say("One sentence."), Say("Two sentences."))
    pieces = chunk_run(nodes, max_chars=2000, pause_style="silence")
    assert len(pieces) == 1


def test_an_oversized_run_splits_at_sentence_boundaries():
    long_sentence = "This is sentence number {n} and it has a few words in it."
    nodes = tuple(Say(long_sentence.format(n=n)) for n in range(1, 6))
    # Each ~60 chars; five of them together exceed a tight limit.
    pieces = chunk_run(nodes, max_chars=140, pause_style="silence")
    assert len(pieces) > 1
    # Every piece is itself under the limit.
    for piece in pieces:
        assert len(render(piece, "silence")) <= 140
    # No sentence lost or duplicated across the split.
    rejoined = " ".join(render(piece, "silence") for piece in pieces)
    for n in range(1, 6):
        assert f"sentence number {n}" in rejoined


def test_splitting_never_happens_mid_sentence_for_ordinary_content():
    """Each produced piece ends on real sentence-terminal punctuation,
    except possibly the very last one (which just ends where the run does)."""
    long_sentence = "This is sentence number {n} with several words in it now."
    nodes = tuple(Say(long_sentence.format(n=n)) for n in range(1, 8))
    pieces = chunk_run(nodes, max_chars=150, pause_style="silence")
    for piece in pieces:
        text = render(piece, "silence").strip()
        assert text.endswith(".")


def test_abbreviations_do_not_cause_a_false_sentence_split():
    """"Dr. Smith" must not be treated as a sentence boundary — the chunker
    consults the same list A.4-A the normalizer does."""
    nodes = (
        Say("Dr."), Say("Smith"), Say("led"), Say("the"), Say("team"),
        Say("in"), Say("1843."),
    )
    pieces = chunk_run(nodes, max_chars=2000, pause_style="silence")
    assert len(pieces) == 1
    assert said(Chunk(nodes=nodes, pause_after_ms=0)) == "Dr. Smith led the team in 1843."


# --- rule 4: an oversized SINGLE sentence ----------------------------------


def test_an_oversized_sentence_splits_at_the_last_clause_boundary():
    words = [f"word{n}," for n in range(1, 30)]  # commas throughout
    words[-1] = "finalword."  # ends the sentence
    nodes = tuple(Say(w) for w in words)
    pieces = chunk_run(nodes, max_chars=80, pause_style="silence")
    assert len(pieces) > 1
    # Every split-off piece except conceivably the last ends on a comma or
    # the sentence's own terminal period -- never mid-word.
    for piece in pieces:
        text = render(piece, "silence").strip()
        assert text.endswith((",", "."))


def test_an_oversized_sentence_with_no_clause_boundary_falls_back_to_a_word_break():
    words = [f"word{n}" for n in range(1, 40)]  # no punctuation at all
    words[-1] = words[-1] + "."
    nodes = tuple(Say(w) for w in words)
    pieces = chunk_run(nodes, max_chars=60, pause_style="silence")
    assert len(pieces) > 1
    rejoined = " ".join(render(piece, "silence") for piece in pieces)
    for n in range(1, 40):
        assert f"word{n}" in rejoined.replace(".", "")


def test_the_word_boundary_fallback_is_flagged_in_verbose():
    words = [f"word{n}" for n in range(1, 40)]
    words[-1] += "."
    nodes = tuple(Say(w) for w in words)
    reporter = Reporter(verbose=True)
    import io
    reporter.stream = io.StringIO()
    chunk_run(nodes, max_chars=60, pause_style="silence", reporter=reporter)
    assert "no clause boundary" in reporter.stream.getvalue()


def test_no_reporter_means_no_crash_on_the_fallback_path():
    words = [f"word{n}" for n in range(1, 40)]
    words[-1] += "."
    nodes = tuple(Say(w) for w in words)
    chunk_run(nodes, max_chars=60, pause_style="silence", reporter=None)  # must not raise


def test_a_single_pathological_token_longer_than_the_limit_is_not_dropped():
    """A giant unbroken token can't be cut without corrupting it -- it goes
    through whole rather than being silently lost."""
    nodes = (Say("x" * 500 + "."),)
    pieces = chunk_run(nodes, max_chars=100, pause_style="silence")
    assert len(pieces) == 1
    assert "x" * 500 in render(pieces[0], "silence")


# --- end to end, through chunk_speech --------------------------------------


def test_pause_after_ms_is_zero_between_mid_run_splits():
    """Only the LAST piece of a split run gets the run's real trailing
    pause; earlier pieces get 0, matching A.3's SENTENCE=0 entry -- the
    engine's own terminal punctuation carries it, not an inserted gap."""
    long_sentence = "This is sentence number {n} and it has a few words in it."
    speech = [Say(long_sentence.format(n=n)) for n in range(1, 6)]
    speech.append(Pause(500))
    chunks = chunk_speech(speech, max_chars=140, pause_style="silence")
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert chunk.pause_after_ms == 0
    assert chunks[-1].pause_after_ms == 500


def test_a_realistic_document_is_mostly_one_chunk_per_run():
    """Against Kokoro's real 2000-char budget, ordinary paragraphs never
    split -- this is the common case the module is built around."""
    from conftest import ARTICLE_HTML

    from readaloud.extract.html import extract_html
    from readaloud.normalize import normalize

    document = extract_html(ARTICLE_HTML, url="https://example.com/tunnel")
    speech = normalize(document)
    chunks = chunk_speech(speech, max_chars=2000, pause_style="silence")

    runs = sum(1 for node in speech if isinstance(node, Pause)) + 1
    assert len(chunks) <= runs + 2  # a little slack, but not a large multiple
    for chunk in chunks:
        assert len(render(list(chunk.nodes), "silence")) <= 2000
