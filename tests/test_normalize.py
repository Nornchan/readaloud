"""One test group per rule in SPEC.md Appendix A.5."""

import pytest
from conftest import ARTICLE_HTML

from readaloud.document import Block, BlockKind, Document
from readaloud.extract.html import extract_html
from readaloud.normalize import (
    NormalizeOptions,
    apply_kind_policy,
    drop_boilerplate,
    drop_duplicates,
    drop_page_numbers,
    drop_running_artifacts,
    drop_title_echo,
    ensure_terminal,
    expand,
    header_nodes,
    is_boilerplate,
    join_hard_wraps,
    normalize,
    repair_line_hyphens,
    repair_unicode,
    segment_spelled,
    strip_citation_markers,
    to_speech,
)
from readaloud.speech import Pause, Pauses, Say, Spell


def para(text, **kwargs):
    return Block(BlockKind.PARAGRAPH, text, **kwargs)


def said(speech):
    return " ".join(n.text for n in speech if isinstance(n, (Say, Spell)))


# --- rule 1 ---------------------------------------------------------------


def test_ligatures_are_repaired():
    assert repair_unicode("ﬁnd the ﬂaw in the aﬀair") == "find the flaw in the affair"


def test_invisible_characters_go():
    assert repair_unicode("soft­hyphen") == "softhyphen"
    assert repair_unicode("zero​width") == "zerowidth"
    assert repair_unicode("non breaking") == "non breaking"


def test_smart_quotes_and_ellipsis():
    assert repair_unicode("“it’s fine”…") == '"it\'s fine".'


def test_emoji_and_bullets_go():
    assert repair_unicode("great 🎉 news") == "great news"
    assert repair_unicode("• a bullet point") == "a bullet point"


def test_newlines_survive_for_the_pdf_rules():
    assert "\n" in repair_unicode("a line\nand another")


# --- rule 2 ---------------------------------------------------------------


def test_running_footer_is_dropped():
    blocks = [para("The Quarterly Review", page=n, position=0.95) for n in (1, 2, 3)]
    blocks.append(para("Real body text goes here.", page=1, position=0.4))
    assert len(drop_running_artifacts(blocks)) == 1


def test_page_varying_footer_collapses_on_digits():
    blocks = [para(f"Proceedings, page {n}", page=n, position=0.95) for n in (1, 2, 3)]
    assert drop_running_artifacts(blocks) == []


def test_side_stamp_away_from_the_edge_is_still_caught():
    """The arXiv stamp runs down the left margin at position ~0.3."""
    blocks = [
        para("arXiv:1810.04805v2 [cs.CL] 24 May 2019", page=n, position=0.30)
        for n in (1, 2, 3, 4)
    ]
    assert drop_running_artifacts(blocks) == []


def test_two_page_repeat_needs_the_edge():
    edge = [para("Chapter One", page=n, position=0.05) for n in (1, 2)]
    middle = [para("Chapter One", page=n, position=0.5) for n in (1, 2)]
    assert drop_running_artifacts(edge) == []
    assert len(drop_running_artifacts(middle)) == 2


def test_long_repeated_text_is_not_an_artifact():
    body = "This paragraph is far too long to be a running header or a footer."
    blocks = [para(body + " " * 0 + " padding words here now", page=n, position=0.9)
              for n in (1, 2, 3)]
    assert len(drop_running_artifacts(blocks)) == 3


# --- rule 3 ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["7", "  12  ", "- 3 -", "xiv", "Page 4"])
def test_page_numbers_at_the_edge_are_dropped(text):
    assert drop_page_numbers([para(text, page=1, position=0.93)]) == []


def test_a_number_in_the_body_survives():
    assert len(drop_page_numbers([para("7", page=1, position=0.5)])) == 1


# --- rules 4 and 5 --------------------------------------------------------


def test_line_break_hyphen_is_joined():
    assert repair_line_hyphens("representa-\ntion", set()) == "representation"


def test_a_real_hyphen_is_kept_when_the_document_proves_it():
    vocabulary = {"self-attention"}
    assert repair_line_hyphens("self-\nattention", vocabulary) == "self-attention"


def test_hard_wraps_become_spaces():
    assert join_hard_wraps("one line\nand the next") == "one line and the next"


# --- rule 6 ---------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Share this on Facebook",
    "Related articles",
    "Photograph: Getty Images",
    "Copyright 2026. All rights reserved.",
    "Sign up for our newsletter",
])
def test_boilerplate_phrases(text):
    assert is_boilerplate(text)


def test_long_prose_is_never_boilerplate():
    prose = (
        "You can read more about the launch in the chapter that follows, which "
        "covers the hydraulic rams, the greased ways, and the three months it "
        "took to move the ship the last few feet into the water at last."
    )
    assert not is_boilerplate(prose)
    assert len(drop_boilerplate([para(prose)])) == 1


# --- rule 7 ---------------------------------------------------------------


def test_truncated_pull_quote_is_dropped():
    body = para(
        "The Thames Tunnel is the greatest work of art in the world, and it "
        "cost more lives than anyone cared to count at the time."
    )
    quote = Block(BlockKind.QUOTE, "the greatest work of art in the world")
    assert drop_duplicates([body, quote]) == [body]


def test_reworded_standfirst_is_dropped():
    body = para("A ship so large that it took three whole months to launch her sideways.")
    standfirst = para("A ship so large it took three months to launch sideways.")
    kept = drop_duplicates([standfirst, body])
    assert kept == [body]


def test_a_long_body_paragraph_is_never_dropped():
    text = " ".join(f"word{index}" for index in range(60))
    blocks = [para(text), para(text)]
    assert len(drop_duplicates(blocks)) == 2


def test_unrelated_blocks_survive():
    blocks = [para("The first tunnel opened in 1843."), para("Marc Brunel patented a shield.")]
    assert len(drop_duplicates(blocks)) == 2


# --- rule 8 ---------------------------------------------------------------


def test_captions_are_dropped_by_default():
    blocks = [Block(BlockKind.CAPTION, "Figure 1: a caption"), para("Body.")]
    assert len(apply_kind_policy(blocks, NormalizeOptions())) == 1


def test_keep_captions_reverses_it():
    blocks = [Block(BlockKind.CAPTION, "Figure 1: a caption"), para("Body.")]
    kept = apply_kind_policy(blocks, NormalizeOptions(keep_captions=True))
    assert len(kept) == 2


def test_consecutive_code_blocks_collapse_to_one_announcement():
    """PEP 8 has 147 of these; announcing each would be worse than silence."""
    blocks = [Block(BlockKind.CODE, f"x = {n}") for n in range(10)]
    kept = apply_kind_policy(blocks, NormalizeOptions())
    assert [block.text for block in kept] == ["Code omitted."]


def test_separated_tables_are_announced_separately():
    blocks = [
        Block(BlockKind.TABLE, "a | b"),
        para("Prose in between."),
        Block(BlockKind.TABLE, "c | d"),
    ]
    kept = apply_kind_policy(blocks, NormalizeOptions())
    assert [block.text for block in kept] == [
        "Table omitted.", "Prose in between.", "Table omitted."
    ]


# --- rule 9 ---------------------------------------------------------------


def test_bracket_citations_go():
    assert strip_citation_markers("as shown [1] and later [2, 3].") == "as shown and later."


def test_superscript_markers_go():
    assert strip_citation_markers("a claim¹ and another²") == "a claim and another"


def test_author_year_is_off_by_default():
    text = "as shown (Smith 2019) in the paper"
    assert strip_citation_markers(text) == text


def test_author_year_when_asked():
    text = "as reported (Peters et al., 2018a; Radford et al., 2018) elsewhere"
    assert strip_citation_markers(text, author_year=True) == "as reported elsewhere"


def test_ordinary_parentheses_survive_author_year():
    text = "the tunnel (which flooded five times) opened late"
    assert strip_citation_markers(text, author_year=True) == text


# --- rules 10 and 11 ------------------------------------------------------


@pytest.mark.parametrize("before, after", [
    ("use a shield, e.g. Brunel's", "use a shield, for example Brunel's"),
    ("the face, i.e. the working end", "the face, that is the working end"),
    ("bricks, mortar, etc.", "bricks, mortar, et cetera"),
    ("Peters et al. showed", "Peters and others showed"),
    ("a 40% increase", "a 40 percent increase"),
    ("from 2019–2024", "from 2019 to 2024"),
    ("see Fig. 3 for detail", "see Figure 3 for detail"),
    ("bread & butter", "bread and butter"),
    ("a rise of 5°", "a rise of 5 degrees"),
])
def test_expansions(before, after):
    assert expand(before) == after


def test_bare_urls_become_link():
    assert expand("see https://example.com/a/b?c=1 for more") == "see link for more"


@pytest.mark.parametrize("text", [
    "running v2.5 in production",
    "the score was 3-2",
    "GPT-4o handled it",
    "a well-known engineer",
    "arriving at 14:05",
    "open report.pdf now",
])
def test_protected_patterns_are_untouched(text):
    assert expand(text) == text


def test_hyphens_are_never_ranges():
    """Only en dashes between digits become "to"."""
    assert expand("the 2019-2024 period") == "the 2019-2024 period"


# --- rule 12 --------------------------------------------------------------


def test_allowlisted_acronyms_are_spelled():
    assert segment_spelled("the US economy") == [Say("the"), Spell("US"), Say("economy")]


def test_pronounceable_acronyms_are_not():
    assert segment_spelled("NASA and UNESCO") == [Say("NASA and UNESCO")]


def test_plurals_keep_their_s():
    assert segment_spelled("the MPs voted") == [
        Say("the"), Spell("MP"), Say("s"), Say("voted")
    ]


def test_who_is_left_alone_because_it_is_ambiguous():
    assert segment_spelled("the WHO said") == [Say("the WHO said")]


# --- rule 13 --------------------------------------------------------------


def test_terminal_punctuation_is_added():
    assert ensure_terminal("How it was built") == "How it was built."
    assert ensure_terminal("Why?") == "Why?"
    assert ensure_terminal("Already done.") == "Already done."


# --- rule 14 --------------------------------------------------------------


def test_header_speaks_title_and_author():
    document = Document(title="The Tunnel", author="Jane Marlow")
    assert said(header_nodes(document)) == "The Tunnel. By Jane Marlow."


def test_header_without_an_author():
    assert said(header_nodes(Document(title="The Tunnel"))) == "The Tunnel."


def test_the_h1_echo_of_the_title_is_dropped():
    blocks = [Block(BlockKind.HEADING, "The Tunnel Under the Thames", level=1), para("Body.")]
    assert len(drop_title_echo(blocks, "The Tunnel Under the Thames")) == 1


def test_an_unrelated_first_heading_survives():
    blocks = [Block(BlockKind.HEADING, "How it was built", level=2), para("Body.")]
    assert len(drop_title_echo(blocks, "The Tunnel Under the Thames")) == 2


# --- rule 15 --------------------------------------------------------------


def test_headings_get_a_pause_either_side():
    blocks = [Block(BlockKind.HEADING, "How it was built", level=2), para("Body text.")]
    speech = to_speech(Document(), blocks, NormalizeOptions(header=False))
    assert speech[0] == Say("How it was built.")
    assert speech[1] == Pause(Pauses.HEADING_AFTER)


def test_a_top_level_heading_gets_the_longer_pause():
    blocks = [para("Intro."), Block(BlockKind.HEADING, "Part One", level=1)]
    speech = to_speech(Document(), blocks, NormalizeOptions(header=False))
    assert Pause(Pauses.SECTION) in speech


def test_list_items_get_their_own_pause():
    blocks = [Block(BlockKind.LIST_ITEM, "the first thing")]
    speech = to_speech(Document(), blocks, NormalizeOptions(header=False))
    assert speech == [Say("the first thing.")]


def test_ordered_lists_speak_their_ordinals():
    blocks = [
        Block(BlockKind.LIST_ITEM, "first thing", ordinal=1),
        Block(BlockKind.LIST_ITEM, "second thing", ordinal=2),
    ]
    speech = to_speech(Document(), blocks, NormalizeOptions(header=False))
    assert said(speech) == "One. first thing. Two. second thing."


def test_unordered_lists_speak_no_marker():
    blocks = [Block(BlockKind.LIST_ITEM, f"thing {n}") for n in range(3)]
    speech = to_speech(Document(), blocks, NormalizeOptions(header=False))
    assert "bullet" not in said(speech).lower()


def test_a_long_list_is_announced():
    blocks = [Block(BlockKind.LIST_ITEM, f"item {n}") for n in range(20)]
    speech = to_speech(Document(), blocks, NormalizeOptions(header=False))
    assert said(speech).startswith("A list of 20 items.")


def test_adjacent_pauses_merge_and_the_ends_are_trimmed():
    blocks = [Block(BlockKind.HEADING, "Title", level=1), para("Body.")]
    speech = to_speech(Document(), blocks, NormalizeOptions(header=False))
    assert not isinstance(speech[0], Pause)
    assert not isinstance(speech[-1], Pause)
    for left, right in zip(speech, speech[1:]):
        assert not (isinstance(left, Pause) and isinstance(right, Pause))


# --- end to end -----------------------------------------------------------


@pytest.fixture
def article_speech():
    document = extract_html(ARTICLE_HTML, url="https://example.com/tunnel")
    return normalize(document), document


def test_the_whole_pipeline_runs(article_speech):
    speech, _ = article_speech
    spoken = said(speech)
    assert spoken.startswith("The Tunnel Under the Thames.")
    assert "tunnelling shield" in spoken
    assert "shortly after opening" not in spoken       # caption dropped
    assert "cookies" not in spoken.lower()             # boilerplate gone
    assert Pause(Pauses.SECTION) in speech


def test_the_title_is_not_said_twice(article_speech):
    speech, _ = article_speech
    assert said(speech).count("The Tunnel Under the Thames") == 1


def test_no_header_drops_the_spoken_byline(article_speech):
    """The page's own H1 is real content and stays; the added header does not."""
    _, document = article_speech
    speech = normalize(document, NormalizeOptions(header=False))
    assert "By Jane Marlow" not in said(speech)
    assert said(article_speech[0]).startswith("The Tunnel Under the Thames. By Jane Marlow.")


def test_decimal_percentages_expand():
    """Regression: the version-string guard used to mask the decimal, so the
    percent rule's lookbehind saw a placeholder instead of a digit."""
    assert expand("a score of 80.5% here") == "a score of 80.5 percent here"


def test_version_strings_are_still_protected():
    assert expand("SQuAD v1.1 and python 3.11.2") == "SQuAD v1.1 and python 3.11.2"


def test_decimal_ranges_use_the_en_dash_rule():
    assert expand("between 2.5–3.5 metres") == "between 2.5 to 3.5 metres"


def test_preprint_stamps_are_dropped():
    """arXiv stamps page one only, so the repetition rule cannot see them."""
    from readaloud.normalize import drop_stamps

    blocks = [
        para("arXiv:1810.04805v2 [cs.CL] 24 May 2019", page=1, position=0.30),
        para("Preprint. Under review.", page=1, position=0.9),
        para("Real body text that should survive intact.", page=1, position=0.4),
    ]
    assert len(drop_stamps(blocks)) == 1


def test_stamps_only_apply_to_pdfs():
    from readaloud.normalize import drop_stamps

    assert len(drop_stamps([para("Preprint. Under review.")])) == 1


def test_emails_are_removed():
    assert expand("write to jane@example.com now") == "write to now"
    assert expand("{a,b}@google.com") == ""
