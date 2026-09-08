"""readaloud.voices — the voices.toml loader (milestone 6).

Kokoro's own accent/gender/table behaviour is exercised in
test_kokoro_backend.py; these tests are about the loader itself: does it
read the packaged file, resolve pairs correctly, and fail usefully on
input the table doesn't cover.
"""

import pytest

from readaloud import voices
from readaloud.errors import AccentUnavailableError


def test_loads_the_packaged_table():
    assert voices.supported_accents("kokoro") == ("us", "uk")
    assert len(voices.voices("kokoro")) == 28


def test_every_accent_name_is_known():
    """config.ACCENTS is the full 8-value --accent choice list; every one of
    them needs a display name for AccentUnavailableError's message, engine
    support or not."""
    from readaloud.config import ACCENTS

    for accent in ACCENTS:
        assert voices.accent_name(accent) != accent or accent in ("us", "uk")
    # us/uk happen to not need the fallback-to-code path either, but every
    # code must resolve to *some* non-empty English name.
    for accent in ACCENTS:
        assert voices.accent_name(accent)


def test_unknown_accent_code_falls_back_to_the_code_itself():
    assert voices.accent_name("xx") == "xx"


def test_resolve_picks_the_first_matching_entry_in_file_order():
    """The table's ordering within a group *is* the preference order —
    resolve() must not re-sort or pick by grade."""
    assert voices.resolve("kokoro", "us", "female") == "af_heart"
    assert voices.resolve("kokoro", "uk", "male") == "bm_fable"


def test_unsupported_accent_names_the_nearest_one():
    with pytest.raises(AccentUnavailableError) as caught:
        voices.resolve("kokoro", "au", "female")
    assert "Australian" in caught.value.message
    assert "--accent uk" in caught.value.hint


def test_nearest_accent_never_points_at_another_unsupported_accent():
    """Every fallback target must itself be in `accents`, or the error
    message would send someone in a circle."""
    supported = set(voices.supported_accents("kokoro"))
    for accent in ("au", "nz", "ie", "za", "in", "ca"):
        assert voices.nearest_accent("kokoro", accent) in supported


def test_unknown_engine_raises():
    with pytest.raises(KeyError):
        voices.voices("not-a-real-engine")


def test_list_voices_returns_the_engines_protocol_type():
    entries = voices.list_voices("kokoro")
    assert all(entry.description.startswith("grade ") for entry in entries)
    ids = {entry.id for entry in entries}
    assert "af_heart" in ids and "bm_daniel" in ids
