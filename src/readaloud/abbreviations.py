"""The shared abbreviation data, per SPEC.md Appendix A.4.

Imported by both the normalizer and the chunker, which have to agree: if the
chunker splits a sentence at "Dr." the prosody breaks audibly, and if the
normalizer expands something the chunker still reads as a sentence end, the
two disagree about where sentences are.

This is data, not logic. Tune it here.
"""

from __future__ import annotations

import re

# --- A: never a sentence end ----------------------------------------------
# A full stop directly after any of these does not end a sentence.

TITLES = {
    "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Rev.", "Fr.", "Sr.", "Jr.", "St.",
    "Hon.", "Gen.", "Col.", "Lt.", "Sgt.", "Capt.", "Adm.", "Gov.", "Sen.",
    "Rep.", "Pres.",
}

SCHOLARLY = {
    "e.g.", "i.e.", "cf.", "vs.", "v.", "viz.", "etc.", "al.", "ibid.",
    "op.", "cit.", "ca.", "approx.",
}

REFERENCE = {
    "Fig.", "Figs.", "Tab.", "No.", "Nos.", "Vol.", "Vols.", "p.", "pp.",
    "ch.", "Ch.", "Sec.", "Eq.", "Ref.", "Refs.", "Ed.", "Eds.", "trans.",
}

ORGANISATION = {"Inc.", "Ltd.", "Co.", "Corp.", "Dept.", "Univ.", "Assn."}

CALENDAR = {
    "Jan.", "Feb.", "Mar.", "Apr.", "Jun.", "Jul.", "Aug.", "Sep.", "Sept.",
    "Oct.", "Nov.", "Dec.", "Mon.", "Tue.", "Tues.", "Wed.", "Thu.", "Thur.",
    "Thurs.", "Fri.", "Sat.", "Sun.",
}

TIME = {"a.m.", "p.m."}

NEVER_SENTENCE_END: frozenset[str] = frozenset(
    TITLES | SCHOLARLY | REFERENCE | ORGANISATION | CALENDAR | TIME
)

# A single capital and a dot is an initial ("J. R. R. Tolkien"), never a
# sentence end.
INITIAL = re.compile(r"\b[A-Z]\.$")


def ends_sentence(token: str) -> bool:
    """Does a full stop on this token actually end a sentence?"""
    if not token.endswith((".", "!", "?")):
        return False
    if token.endswith(("!", "?")):
        return True
    if token in NEVER_SENTENCE_END:
        return False
    # Case-insensitive fallback: "Etc." at the start of a clause.
    if token.lower() in {entry.lower() for entry in NEVER_SENTENCE_END}:
        return False
    if INITIAL.search(token):
        return False
    return True


# --- B: expansions ---------------------------------------------------------
# (pattern, replacement). Order matters: longer forms first, so "et al."
# is not half-eaten by the "al." entry.

EXPANSIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bet\s+al\.(?=\s|$|,)", re.IGNORECASE), "and others"),
    (re.compile(r"\be\.\s?g\.(?=\s|$|,)", re.IGNORECASE), "for example"),
    (re.compile(r"\bi\.\s?e\.(?=\s|$|,)", re.IGNORECASE), "that is"),
    (re.compile(r"\betc\.(?=\s|$|,)", re.IGNORECASE), "et cetera"),
    (re.compile(r"\bcf\.(?=\s|$|,)", re.IGNORECASE), "compare"),
    (re.compile(r"\bvs?\.(?=\s)", re.IGNORECASE), "versus"),
    (re.compile(r"\bapprox\.(?=\s|$)", re.IGNORECASE), "approximately"),
    # Only where a number follows — "Fig." alone might be prose.
    (re.compile(r"\bFigs\.(?=\s*\d)"), "Figures"),
    (re.compile(r"\bFig\.(?=\s*\d)"), "Figure"),
    (re.compile(r"\bNos\.(?=\s*\d)"), "Numbers"),
    (re.compile(r"\bNo\.(?=\s*\d)"), "Number"),
    (re.compile(r"\bpp\.(?=\s*\d)"), "pages"),
    (re.compile(r"\bp\.(?=\s*\d)"), "page"),
    # Symbols.
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"(?<=\s)&(?=\s)"), "and"),
    (re.compile(r"§\s*(?=\d)"), "section "),
    (re.compile(r"(?<=\d)\s*°"), " degrees"),
    (re.compile(r"±"), "plus or minus"),
    (re.compile(r"≈"), "approximately"),
    (re.compile(r"×"), "by"),
    # An en dash between two numbers is a range. A hyphen is not — never
    # touch hyphens, or hyphenated compounds get mangled.
    (re.compile(r"(?<=\d)\s*–\s*(?=\d)"), " to "),
]

# Email addresses are never worth reading aloud. The braced form is an
# academic-PDF convention for a shared domain and would otherwise leave the
# local part behind: "{jacobdevlin,kentonl}@link".
EMAIL_PATTERN = re.compile(
    r"\{[^{}\s]+\}@[\w.-]+\.\w{2,}"
    r"|[\w.+-]+@[\w.-]+\.\w{2,}"
)

# Bare URLs are read as "link" rather than spelled out character by character.
URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'\)\]]+|\b[\w.-]+\.(?:com|org|net|edu|gov|io|co\.uk)"
    r"(?:/[^\s<>\"'\)\]]*)?",
    re.IGNORECASE,
)


# --- C: spell out ----------------------------------------------------------
# An allowlist, never inferred from casing: NASA, LASER, SCUBA and UNESCO are
# all-caps and all spoken as words. WHO is excluded on purpose — the
# organisation and the pronoun are genuinely ambiguous.

SPELL_OUT: frozenset[str] = frozenset(
    """
    US USA UK EU UN
    AI ML API URL URI HTTP HTTPS SQL CSS HTML XML JSON PDF
    CPU GPU RAM SSD USB OS IP DNS
    CEO CFO CTO COO HR PR IT UI UX
    MP PhD NHS BBC ITV FBI CIA IRS FDA
    GDP CPI ID DNA RNA HIV AGM CV
    """.split()
)

# Matches a candidate token plus an optional plural/possessive tail, so "MPs"
# becomes Spell("MP") + Say("s") rather than being missed entirely.
SPELL_TOKEN = re.compile(r"\b([A-Z]{2,5})('s|s)?\b")


# --- D: protected patterns -------------------------------------------------
# Nothing in B or C may touch a match. Masked before expansion, restored after.

PROTECTED: list[re.Pattern[str]] = [
    # Version strings and identifiers only — NOT bare decimals. Protecting
    # every "80.5" stopped the percent rule firing, because its lookbehind
    # then saw the mask instead of a digit.
    re.compile(r"\bv\d+(?:\.\d+)+\b"),                # v2.5, v1.1
    re.compile(r"\b\d+\.\d+\.\d+\b"),                 # 3.11.2
    re.compile(r"\b\d{4}\.\d{4,5}\b"),                # arXiv ids
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),      # 14:05
    re.compile(r"\b\d{1,2}\.\d{2}\s?[ap]\.?m\.?\b", re.IGNORECASE),
    re.compile(r"\b\d+-\d+\b"),                       # 3-2, page ranges
    re.compile(r"\b[A-Za-z]+-\d+[A-Za-z0-9.]*\b"),    # GPT-4o, en-GB-2
    re.compile(r"\b\w+\.(?:pdf|html?|txt|md|py|js|csv|json|xml|zip)\b", re.IGNORECASE),
]
