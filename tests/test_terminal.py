"""Reporter.progress: a rewriting bar on a TTY, periodic lines when piped.

Section 1's UX requirements state both "no progress bar when piped" and
"silence is unacceptable" during a multi-minute synthesis — these are only
in tension if "progress bar" means any progress output at all rather than
specifically the carriage-return bar.
"""

import io

from readaloud.terminal import Reporter


class FakeTTYStream(io.StringIO):
    def isatty(self):
        return True


def test_tty_gets_a_rewriting_bar():
    stream = FakeTTYStream()
    reporter = Reporter(stream=stream)
    reporter.progress(1, 10, "synthesizing")
    reporter.progress(5, 10, "synthesizing")
    out = stream.getvalue()
    assert out.count("\r") == 2
    assert "\n" not in out
    assert "[" in out and "]" in out


def test_non_tty_never_emits_a_carriage_return():
    stream = io.StringIO()  # isatty() is False by default
    reporter = Reporter(stream=stream)
    for done in range(1, 21):
        reporter.progress(done, 20, "synthesizing")
    out = stream.getvalue()
    assert "\r" not in out


def test_non_tty_is_never_silent_across_a_long_run():
    stream = io.StringIO()
    reporter = Reporter(stream=stream)
    for done in range(1, 101):
        reporter.progress(done, 100, "synthesizing")
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) >= 5  # not one line at the very end, not 100 either
    assert len(lines) < 30


def test_non_tty_always_shows_the_first_and_last():
    stream = io.StringIO()
    reporter = Reporter(stream=stream)
    for done in range(1, 8):
        reporter.progress(done, 7, "x")
    out = stream.getvalue()
    assert "1/7" in out
    assert "7/7" in out


def test_zero_total_is_a_no_op():
    stream = io.StringIO()
    Reporter(stream=stream).progress(0, 0, "x")
    assert stream.getvalue() == ""


def test_progress_done_only_prints_on_a_tty():
    tty = FakeTTYStream()
    Reporter(stream=tty).progress_done()
    assert tty.getvalue() == "\n"

    piped = io.StringIO()
    Reporter(stream=piped).progress_done()
    assert piped.getvalue() == ""
