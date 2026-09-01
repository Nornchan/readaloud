"""Guard the golden fixtures.

Skipped unless the corpus has been built (`python scripts/corpus.py --fetch`),
so a clean checkout still runs the suite offline. The fixtures themselves are
committed, so a rule change shows up as a corpus-wide diff in review.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"

# Captured at import, before the isolated_home fixture redirects HOME: the
# corpus and the fetch cache live in the real one, and regenerating a URL
# fixture against an empty cache would re-fetch a live article that has since
# been edited.
REAL_ENV = dict(os.environ)


def _corpus_present() -> bool:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from corpus import CORPUS
    except ImportError:
        return False
    return (CORPUS / "pdf" / "bert.pdf").exists()


pytestmark = pytest.mark.skipif(
    not _corpus_present(), reason="run `python scripts/corpus.py --fetch` first"
)


def test_fixtures_exist():
    fixtures = list(GOLDEN.glob("*.speech"))
    assert len(fixtures) == 16, "one fixture per corpus document"


def test_normalization_output_is_unchanged():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "golden.py")],
        capture_output=True, text=True, env=REAL_ENV,
    )
    assert result.returncode == 0, (
        "normalization output drifted from the golden fixtures:\n"
        + result.stdout
        + "\nRun `python scripts/golden.py --diff all` to see what changed, "
        "then `--write` if you meant it."
    )


def test_error_cases_are_recorded_too():
    assert "NoTextLayerError" in (GOLDEN / "pdf-scan.speech").read_text()
    assert "FetchError" in (GOLDEN / "url-telegraph.speech").read_text()
