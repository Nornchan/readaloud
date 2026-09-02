"""The per-chunk synthesis cache.

Every engine is local now (see SPEC.md Appendix B), so this buys wall-clock
time, not API cost: killing the process mid-synthesis and re-running should
resume from cache rather than re-paying for every chunk already rendered,
and re-running the same article while tuning normalization rules should cost
nothing the second time. Keyed by hash(text + voice + speed + engine),
exactly as SPEC.md section 2 specifies.

Mirrors fetch.py's cache philosophy — a corrupt or unreadable entry is
treated as absent, never as a reason to fail the run — with one addition
fetch.py doesn't need: writes are atomic (write to a temp file, then
rename). A truncated *webpage* cache entry just gets refetched next time; a
truncated *audio* entry read as if complete would splice broken audio into
the middle of an otherwise good file, silently. Atomic rename means a killed
process never leaves a partial entry for the next run to trust.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from readaloud.config import cache_home


def cache_dir() -> Path:
    return cache_home() / "synth"


def chunk_key(text: str, voice: str, speed: float, engine: str) -> str:
    payload = f"{engine}\x00{voice}\x00{speed}\x00{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def read(key: str) -> bytes | None:
    path = cache_dir() / f"{key}.audio"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None  # an empty file is not a valid hit


def write(key: str, data: bytes) -> None:
    if not data:
        return
    try:
        directory = cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{key}.audio"
        # Unique per-process temp name: two concurrent readaloud invocations
        # racing on the same chunk must not corrupt each other's write.
        tmp = directory / f".{key}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        tmp.replace(target)  # atomic on the same filesystem
    except OSError:
        pass  # A cache we can't write is a slow re-run, not a broken one.
