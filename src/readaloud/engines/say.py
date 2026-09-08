"""macOS `say` — a development backend.

Not a shipping engine: the voices are dated and it exists only so the
normalizer's output can be *listened to* while the rules are being written,
rather than read as text and hoped about. It is genuinely useful for that,
because `say` speaks embedded commands — `[[slnc 500]]` for silence and
`[[char LTRL]]` for letter-by-letter — which map exactly onto the speech IR's
Pause and Spell nodes.

Needs no API key and no network, which also makes it the backend the test
suite can exercise end to end.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from readaloud.engines import Voice
from readaloud.errors import DependencyError, SynthesisError

# macOS reports a locale per voice but not a gender, so the common ones are
# named here. Anything unlisted still works, it just cannot be selected by
# --gender.
_KNOWN_GENDERS = {
    "female": {
        "Kate", "Serena", "Fiona", "Stephanie", "Samantha", "Allison", "Ava",
        "Susan", "Zoe", "Karen", "Moira", "Veena", "Tessa", "Catherine",
        "Isha", "Nicky", "Joelle", "Sandy", "Shelley",
    },
    "male": {
        "Daniel", "Oliver", "Alex", "Tom", "Fred", "Evan", "Nathan", "Lee",
        "Rishi", "Gordon", "Jamie", "Aaron", "Ralph", "Junior",
    },
}

_LOCALE_TO_ACCENT = {
    "en_GB": "uk", "en_US": "us", "en_AU": "au", "en_IE": "ie",
    "en_IN": "in", "en_ZA": "za", "en_NZ": "nz", "en_CA": "ca",
}

# `say -v ?` prints: name, whitespace, locale, whitespace, "# sample text".
_VOICE_LINE = re.compile(r"^(?P<name>.+?)\s{2,}(?P<locale>[a-z]{2}_[A-Z]{2})\s+#")

# macOS `say -r` is words per minute; its default is about this.
_BASE_RATE = 175


class SayBackend:
    """The `TTSBackend` protocol, backed by /usr/bin/say."""

    name = "say"
    max_chars = 100_000  # `say` reads from a file; there is no request limit
    supports_ssml = False
    pause_style = "say"

    def __init__(self) -> None:
        if not shutil.which("say"):
            raise DependencyError(
                "the `say` command was not found",
                "The say backend is macOS-only. Use --engine kokoro instead.",
            )

    def list_voices(self) -> list[Voice]:
        try:
            result = subprocess.run(
                ["say", "-v", "?"], capture_output=True, text=True, check=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SynthesisError(
                f"could not list `say` voices: {exc}",
                "Check that /usr/bin/say works from your shell.",
            ) from exc

        voices: list[Voice] = []
        for line in result.stdout.splitlines():
            match = _VOICE_LINE.match(line)
            if not match:
                continue
            locale = match.group("locale")
            accent = _LOCALE_TO_ACCENT.get(locale)
            if accent is None:
                continue  # not an English accent we offer
            name = match.group("name").strip()
            bare = name.split(" (")[0]
            gender = next(
                (key for key, names in _KNOWN_GENDERS.items() if bare in names), None
            )
            voices.append(
                Voice(id=name, name=bare, accent=accent, gender=gender,
                      description=locale)
            )
        return voices

    def resolve_voice(self, accent: str, gender: str) -> str:
        """Best `say` voice for an accent/gender pair, with fallbacks."""
        voices = self.list_voices()
        for candidate in voices:
            if candidate.accent == accent and candidate.gender == gender:
                return candidate.id
        for candidate in voices:
            if candidate.accent == accent:
                return candidate.id
        for candidate in voices:
            if candidate.gender == gender:
                return candidate.id
        if voices:
            return voices[0].id
        raise SynthesisError(
            "no English `say` voices are installed",
            "Add one in System Settings → Accessibility → Spoken Content.",
        )

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        """Render to AIFF bytes.

        The text goes via a file rather than an argument: a whole article
        would otherwise overflow the command-line length limit.
        """
        rate = max(90, min(500, round(_BASE_RATE * speed)))
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "input.txt"
            target = Path(workspace) / "output.aiff"
            source.write_text(text, encoding="utf-8")
            command = [
                "say", "-v", voice, "-r", str(rate),
                "-o", str(target), "-f", str(source),
            ]
            try:
                result = subprocess.run(command, capture_output=True, text=True)
            except OSError as exc:
                raise SynthesisError(f"could not run `say`: {exc}") from exc
            if result.returncode != 0 or not target.exists():
                raise SynthesisError(
                    f"`say` failed: {result.stderr.strip() or 'no output produced'}",
                    f"Check that the voice {voice!r} is installed "
                    "(`say -v ?` lists them).",
                )
            return target.read_bytes()
