from readaloud.speech import (
    Pause,
    Pauses,
    Say,
    Spell,
    char_count,
    duration_estimate,
    head,
    render,
    serialize,
    transcript,
    word_count,
)

SPEECH = [
    Say("The tunnel opened in 1843."),
    Pause(Pauses.PARAGRAPH),
    Say("It was funded by the"),
    Spell("US"),
    Say("government."),
]


def test_counts_ignore_pauses():
    assert word_count(SPEECH) == 5 + 5 + 1 + 1
    assert char_count(SPEECH) > 0


def test_duration_includes_the_pauses():
    with_pause = duration_estimate(SPEECH, 150, 1.0)
    without = duration_estimate([node for node in SPEECH if not isinstance(node, Pause)],
                                150, 1.0)
    assert with_pause > without


def test_speed_shortens_the_estimate():
    assert duration_estimate(SPEECH, 150, 2.0) < duration_estimate(SPEECH, 150, 1.0)


def test_head_cuts_at_a_node_boundary():
    long_speech = [Say(" ".join(["word"] * 100)), Pause(500), Say("tail")]
    assert head(long_speech, 150) == long_speech
    assert head(long_speech, 50) == [Say(" ".join(["word"] * 100))]


def test_ssml_rendering():
    out = render(SPEECH, "ssml")
    assert '<break time="500ms"/>' in out
    assert '<say-as interpret-as="characters">US</say-as>' in out


def test_ssml_escapes():
    assert "&amp;" in render([Say("bread & butter")], "ssml")


def test_tag_rendering_picks_the_nearest_tag():
    assert "[pause]" in render([Say("a"), Pause(500), Say("b")], "tag")
    assert "[long pause]" in render([Say("a"), Pause(1200), Say("b")], "tag")
    assert "[short pause]" in render([Say("a"), Pause(300), Say("b")], "tag")


def test_say_rendering_uses_embedded_commands():
    out = render(SPEECH, "say")
    assert "[[slnc 500]]" in out
    assert "[[char LTRL]]US[[char NORM]]" in out


def test_silence_style_sends_no_pause_markup():
    """Piper cannot speak a pause, so it becomes real silence at stitch time."""
    out = render(SPEECH, "silence")
    assert "500" not in out
    assert "break" not in out
    assert "U. S." in out


def test_serialize_is_one_node_per_line():
    lines = serialize(SPEECH).splitlines()
    assert lines[0].startswith("SAY   ")
    assert lines[1] == "PAUSE 500"
    assert "SPELL US" in lines


def test_transcript_turns_pauses_into_paragraphs():
    text = transcript(SPEECH)
    assert "\n\n" in text
    assert text.endswith("\n")
