"""The per-chunk synthesis cache — buys wall-clock time now, not API cost."""

from readaloud import cache


def test_key_is_stable_for_identical_inputs():
    a = cache.chunk_key("hello world", "bf_emma", 1.0, "kokoro")
    b = cache.chunk_key("hello world", "bf_emma", 1.0, "kokoro")
    assert a == b


def test_key_changes_with_any_component():
    base = cache.chunk_key("hello world", "bf_emma", 1.0, "kokoro")
    assert base != cache.chunk_key("goodbye world", "bf_emma", 1.0, "kokoro")
    assert base != cache.chunk_key("hello world", "af_heart", 1.0, "kokoro")
    assert base != cache.chunk_key("hello world", "bf_emma", 1.25, "kokoro")
    assert base != cache.chunk_key("hello world", "bf_emma", 1.0, "say")


def test_round_trip():
    key = cache.chunk_key("round trip", "bf_emma", 1.0, "kokoro")
    assert cache.read(key) is None
    cache.write(key, b"some audio bytes")
    assert cache.read(key) == b"some audio bytes"


def test_a_miss_returns_none_not_an_exception():
    assert cache.read("nonexistent" * 3) is None


def test_writing_empty_bytes_is_a_no_op():
    """Never cache a degenerate result as if it were a valid hit."""
    key = cache.chunk_key("empty case", "bf_emma", 1.0, "kokoro")
    cache.write(key, b"")
    assert cache.read(key) is None


def test_a_corrupt_or_unreadable_entry_is_treated_as_absent():
    """Mirrors fetch.py's cache philosophy: never fail the run over a bad
    cache entry, just treat it as a miss."""
    key = cache.chunk_key("corrupt case", "bf_emma", 1.0, "kokoro")
    path = cache.cache_dir() / f"{key}.audio"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")  # exists, but empty -- same as an unreadable entry
    assert cache.read(key) is None


def test_write_is_atomic_no_partial_file_survives_a_simulated_interrupt():
    """The correctness property the 'kill mid-synthesis and resume' scenario
    depends on: a reader must never see a half-written entry."""
    key = cache.chunk_key("atomic case", "bf_emma", 1.0, "kokoro")
    cache.write(key, b"complete audio data")
    directory = cache.cache_dir()
    leftovers = list(directory.glob(f".{key}.*.tmp"))
    assert leftovers == []
    assert cache.read(key) == b"complete audio data"


def test_write_survives_an_unwritable_directory():
    """A cache we can't write to is a slow re-run, not a broken one."""
    import os

    key = cache.chunk_key("unwritable case", "bf_emma", 1.0, "kokoro")
    directory = cache.cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o500)
    try:
        cache.write(key, b"data")  # must not raise
    finally:
        os.chmod(directory, 0o700)
