# SPEC.md — `readaloud`

A macOS command-line tool that turns a web article, HTML file, or PDF into a
listenable audio file, using the most human-sounding text-to-speech available,
with selectable English accent and voice gender. Distributed via Homebrew.

Implement this spec. Where I've left a choice open, make the call and tell me
what you picked and why. Where I've named a specific library, use it unless it's
unmaintained — then say so and propose an alternative before switching.

---

## 1. What the user types

```
readaloud <source> [options]
```

`<source>` is one of:
- a URL (`https://example.com/article`)
- a path to a local `.html` file
- a path to a local `.pdf` file

Detect the type from the argument itself. Don't make the user pass a `--type` flag.

### Options

| Flag | Default | Behaviour |
|---|---|---|
| `--accent`, `-a` | `uk` | `us`, `uk`, `au`, `ie`, `in`, `za`, `nz`, `ca` |
| `--gender`, `-g` | `female` | `female`, `male` |
| `--voice`, `-v` | — | Exact voice ID; overrides `--accent`/`--gender` |
| `--engine`, `-e` | from config | Which TTS backend to use |
| `--speed` | `1.0` | `0.5`–`2.0` |
| `--out`, `-o` | derived from title | Output path |
| `--format`, `-f` | `mp3` | `mp3`, `m4a`, `wav` |
| `--list-voices` | — | Print available voices for the active engine, grouped by accent |
| `--preview` | — | Synthesize only the first ~150 words, play it, exit |
| `--estimate` | — | Print word count, character count, estimated cost and runtime. Synthesize nothing |
| `--keep-text` | — | Write the extracted plain text next to the audio file |
| `--no-cache` | — | Bypass the cache |
| `--verbose` | — | Show each pipeline stage |

`readaloud config` opens the config file in `$EDITOR`.
`readaloud --version`, `readaloud --help` behave conventionally.

### UX requirements

- Progress bar during synthesis showing chunk N of M. Long articles take minutes;
  silence is unacceptable.
- On success, print the output path and duration, nothing else.
- Errors go to stderr with a non-zero exit code. A paywalled page, a scanned PDF
  with no text layer, and a missing API key are three different errors with three
  different messages, each saying what the user can do about it.
- Respect `NO_COLOR` and non-TTY output (no progress bar when piped).

---

## 2. Pipeline

```
source → fetch → extract → normalize → chunk → synthesize → stitch → tag → output
```

**Fetch.** For URLs: `httpx`, real browser User-Agent, follow redirects, 30s
timeout. Cache raw responses in `~/.cache/readaloud/`.

**Extract.**
- HTML (fetched or local): `trafilatura` for main-content extraction. It's the
  best of the readability-style extractors and gives you title, author, and date.
- PDF: `PyMuPDF`. Detect a missing text layer (near-zero extractable characters)
  and fail with a clear message rather than producing an empty file. Do *not*
  add OCR in v1.

**Normalize.** This stage is what separates a tool people use from one they
abandon after two articles. Text that reads fine looks terrible out loud.

- Strip navigation, cookie notices, "Share this", author bios, comment sections,
  and "Related articles" blocks that survived extraction.
- Drop image captions and figure labels by default.
- Repair PDF artifacts: hyphens at line breaks, hard-wrapped lines mid-sentence,
  running headers and footers repeated on every page, page numbers on their own line.
- Remove footnote/citation markers (`[1]`, `¹`, `(Smith 2019)` optionally).
- Expand what TTS reads badly: `e.g.` → "for example", `i.e.` → "that is",
  `%` → "percent", `$5M` → "five million dollars", `2019–2024` → "2019 to 2024".
- Replace bare URLs with "link" rather than reading the characters aloud.
- Convert headings into a spoken pause plus the heading text, using SSML
  `<break>` where the engine supports it.
- Insert a short pause between paragraphs.

Put every rule in one module with unit tests. I will want to tune these.

**Chunk.** Split on sentence boundaries to stay under the engine's per-request
character limit (varies by engine; make it a property of the backend). Never
split mid-sentence — the prosody break is audible.

**Synthesize.** Concurrent requests with a bounded worker pool. Retry with
exponential backoff on 429 and 5xx. Cache each chunk keyed by
`hash(text + voice + speed + engine)` so a re-run after a failure doesn't
re-pay for the whole article.

**Stitch.** Concatenate chunk audio with `ffmpeg`, then normalize loudness
(`loudnorm` filter, target -16 LUFS — the podcast convention).

**Tag.** Write ID3 tags: title from the article, artist from the author,
album `readaloud`, comment holding the source URL.

---

## 3. TTS backends

Define a `TTSBackend` protocol — `list_voices()`, `synthesize(text, voice, speed) -> bytes`,
`max_chars`, `supports_ssml` — and implement backends behind it. Register them
in an entry-point-style map so adding one is a single new file.

Implement **two** for v1:

1. **A hosted neural engine as the default.** Pick from ElevenLabs, Azure Neural
   TTS, Google Cloud TTS, or OpenAI TTS. My priorities in order: how human it
   sounds on long-form prose, then breadth of English accents, then cost. Azure
   and Google have by far the widest accent catalogues; ElevenLabs generally
   wins on realism. Research the current state of each and recommend one — don't
   assume the situation is what your training data says it is.
2. **An offline engine** so the tool still works with no API key and no network.
   Look at Piper and Kokoro. Quality will be lower; that's expected and fine.

Voice selection: ship a `voices.yaml` mapping `(engine, accent, gender)` →
concrete voice ID, with a preferred voice and fallbacks per combination. The
mapping is data, not code, so I can edit it without touching Python.

**API keys.** Read from environment variable first, then
`~/.config/readaloud/config.toml`. Never from a command-line flag (it leaks into
shell history) and never committed anywhere. If a key is missing, say exactly
which environment variable to set.

---

## 4. Homebrew packaging

Python CLI, so:

- Target `python@3.12`. Structure as a standard `pyproject.toml` package with a
  console entry point.
- Formula uses `include Language::Python::Virtualenv` and
  `virtualenv_install_with_resources`.
- `depends_on "ffmpeg"` and `depends_on "python@3.12"`.
- Generate the `resource` stanzas with `brew update-python-resources` rather
  than writing them by hand. Keep the dependency tree small — every dependency
  becomes a resource block someone has to maintain.
- Ship in a personal tap: repo `homebrew-readaloud` under my GitHub account, so
  users run `brew install <user>/readaloud/readaloud`.
- Include a `test do` block that actually exercises the tool — synthesize a
  short fixed string with the offline engine and assert the output file exists
  and is non-empty. A test that only checks `--version` is worthless.
- Add a `caveats` block explaining API key setup.
- Formula must pass `brew audit --strict --online --new` and
  `brew install --build-from-source`.

Note for planning, not for you to solve: homebrew-core has notability
requirements (a repo needs meaningful stars/forks/watchers), so a personal tap
is the realistic distribution route for a while.

---

## 5. Build order

Do these as separate milestones. Stop after each and show me the result — don't
build the whole thing in one pass.

1. Skeleton: CLI parsing, config loading, `--help`, source-type detection. No audio.
2. Extraction: URL, HTML, and PDF → clean plain text. `--keep-text` works.
   Test against 5 real articles of each type, including one paywalled page and
   one two-column academic PDF.
3. Normalization module with unit tests.
4. One TTS backend end-to-end, single chunk, no concurrency. First real audio.
5. Chunking, concurrency, caching, stitching, loudness normalization, tagging.
6. Second backend + `voices.yaml` + `--list-voices` + accent/gender selection.
7. Homebrew formula and tap.

---

## 6. Acceptance criteria

- A 3,000-word news article at a URL produces a single MP3 that plays start to
  finish with no truncation, no repeated sentences, and no audible seams at
  chunk boundaries.
- A 20-page PDF with two-column layout produces text in correct reading order.
- Changing `--accent uk` to `--accent in` audibly changes the accent.
- `--estimate` on a long article completes in under two seconds and makes zero
  synthesis calls.
- Killing the process mid-synthesis and re-running resumes from cache rather
  than re-synthesizing completed chunks.
- `brew audit --strict --online --new` passes clean.

---

## 7. Ask me before you start

1. Which hosted TTS engine you recommend, with current pricing and a note on
   which one you'd actually pick after researching.
2. Whether the offline engine is worth the packaging weight in v1.
3. Anything in the normalization list you think is wrong or missing.

---

# Appendix A — Pipeline contracts

*Written after milestone 2, before the normalizer exists. Everything in this
appendix is a contract between stages: change it deliberately, not by
accident. Section A.2 is implemented; A.3 onwards is the design the normalizer
will be built against.*

## A.1 Where the boundaries are

```
                    structural IR                speech IR
                  (Document/Block)          (Utterance node list)
                         │                          │
  source → fetch → extract → normalize → chunk → synthesize → stitch → tag
                         │                          │            │
                    A.2 contract              A.3 contract   A.3 rendering
```

Two rules hold the design together:

1. **Extraction never flattens.** A heading, a pull quote and a paragraph are
   distinguishable all the way into the normalizer. Once merged into a string
   they cannot be told apart again.
2. **The normalizer never emits SSML.** It emits engine-neutral nodes. Each
   backend renders them in its own dialect. Otherwise adding a third backend
   means rewriting the normalizer — and the two backends we have already
   disagree: Azure takes full SSML, Eleven v3 dropped SSML for `[pause]` tags,
   and Piper has neither.

## A.2 The structural IR — extraction → normalization

`readaloud.document`. This exists and is under test.

```python
class BlockKind(Enum):
    TITLE | HEADING | PARAGRAPH | LIST_ITEM | QUOTE | CAPTION | TABLE | CODE

@dataclass
class Block:
    kind:     BlockKind
    text:     str
    level:    int          # heading depth 1–6, or list nesting; 0 if n/a
    ordinal:  int | None   # 1-based position in an ordered list
    page:     int | None   # 1-based; PDF only
    position: float | None # top edge as a fraction of page height; PDF only

@dataclass
class Document:
    blocks: list[Block]
    title, author, date, url, path, page_count
```

**Invariants the extractor guarantees:**

| # | Invariant | Why the normalizer needs it |
|---|---|---|
| 1 | Blocks are in reading order — two-column PDFs are un-interleaved before this point | Sentence continuation across blocks is meaningful |
| 2 | PDF block text **keeps its original newlines** | Hard-wrap and line-break-hyphen repair need to see where lines broke |
| 3 | HTML block text is already whitespace-collapsed | No line structure exists there to preserve |
| 4 | `page` and `position` are set for every PDF block | Running header/footer detection keys off exactly these two |
| 5 | Captions are labelled `CAPTION`, never silently discarded | "Dropped by policy" must stay a decision the normalizer can be told to reverse |
| 6 | `TITLE` is never emitted by extraction | It is reserved for the normalizer's spoken header (A.5 rule 14) |
| 7 | Entities are resolved; ligatures, soft hyphens and zero-width characters are **not** repaired | Unicode hygiene is a normalizer rule, in one place, testable |

**Known gaps, documented rather than hidden:** PDF tables are not detected and
arrive as `PARAGRAPH`; PDF math arrives as fragmented blocks in unreliable
order; a sentence spanning a page boundary arrives as two blocks.

## A.3 The speech IR — normalization → chunking → synthesis

```python
@dataclass(frozen=True)
class Say:    text: str    # spoken as written
@dataclass(frozen=True)
class Spell:  text: str    # letter by letter: "US" → "U. S."
@dataclass(frozen=True)
class Pause:  ms: int      # silence

Utterance = Say | Spell | Pause
Speech    = list[Utterance]
```

Three node types, no more. Every rule in A.5 is expressible in them, and
anything richer (pitch, emphasis, per-word phonemes) is unsupported by at
least two of the three backends.

**Pause vocabulary.** Named, so tuning happens in one place:

| Name | ms | Inserted |
|---|---|---|
| `SENTENCE` | 0 | Never — engines handle terminal punctuation themselves |
| `LIST_ITEM` | 300 | After each list item |
| `PARAGRAPH` | 500 | Between paragraphs |
| `HEADING_AFTER` | 400 | After a heading, before its body |
| `HEADING_BEFORE` | 900 | Before a heading |
| `SECTION` | 1200 | After the spoken header; before an H1 |

**Backend rendering.** Each backend declares `pause_style`:

| Node | `ssml` (Azure) | `tag` (Eleven v3) | `silence` (Piper, Eleven Flash) |
|---|---|---|---|
| `Say` | text, XML-escaped | text | text |
| `Spell` | `<say-as interpret-as="characters">` | letters joined by `". "` | letters joined by `". "` |
| `Pause(ms)` | `<break time="500ms"/>` | nearest of `[short pause]` / `[pause]` / `[long pause]` | **a real silent segment inserted at stitch time** |

The `silence` style is why `Pause` is a first-class node rather than markup:
we already run ffmpeg to concatenate chunks, so a pause an engine cannot speak
becomes literal silence in the stitch. No backend has to fake it.

**Chunker contract:**

1. Chunks are split at `Pause` boundaries first, then at sentence boundaries
   inside a `Say`, and **never** mid-sentence.
2. Sentence splitting consults list A.4-A. A `.` following any entry there is
   not a sentence end.
3. A `Pause` at a chunk boundary is not sent to the engine — it becomes
   silence at stitch time regardless of `pause_style`, which is also what
   removes the audible seam.
4. A single sentence longer than `max_chars` is split at the last clause
   boundary (`;` `:` `,` `—`) before the limit; if there is none, it is split
   at the last word boundary and flagged in `--verbose`.

## A.4 The shared abbreviation list

One module, `readaloud.abbreviations`, imported by both the normalizer and the
chunker. They must agree: if the chunker splits a sentence at `Dr.` the
prosody breaks, and if the normalizer expands something the chunker still
treats as a sentence end, they disagree about where sentences are.

**A — never a sentence end** (chunker: do not split; normalizer: no pause)

| Group | Entries |
|---|---|
| Titles | Mr. Mrs. Ms. Dr. Prof. Rev. Fr. Sr. Jr. St. Hon. Gen. Col. Lt. Sgt. Capt. Adm. Gov. Sen. Rep. Pres. |
| Scholarly | e.g. i.e. cf. vs. viz. etc. et al. ibid. op. cit. ca. approx. |
| Reference | Fig. Figs. Tab. No. Nos. Vol. Vols. p. pp. ch. Sec. Eq. Ref. Refs. Ed. Eds. trans. |
| Organisation | Inc. Ltd. Co. Corp. Dept. Univ. Assn. |
| Calendar | Jan. Feb. Mar. Apr. Jun. Jul. Aug. Sep. Sept. Oct. Nov. Dec. Mon. Tue. Wed. Thu. Fri. Sat. Sun. |
| Time | a.m. p.m. |

Matching is case-sensitive on the first letter and requires the trailing dot.
A single capital letter plus a dot (`J.`) is always treated as an initial, not
a sentence end.

**B — expansions** (normalizer only; each entry gets its own unit test)

| From | To | Condition |
|---|---|---|
| `e.g.` | "for example" | any |
| `i.e.` | "that is" | any |
| `etc.` | "et cetera" | any |
| `cf.` | "compare" | any |
| `et al.` | "and others" | any |
| `vs.` / `v.` | "versus" | any |
| `approx.` | "approximately" | any |
| `Fig.` / `Figs.` | "Figure" / "Figures" | followed by a digit |
| `No.` | "Number" | followed by a digit |
| `%` | "percent" | preceded by a digit |
| `&` | "and" | not inside a word (`AT&T` is left alone) |
| `§` | "section" | any |
| `°` | "degrees" | preceded by a digit |
| `±` | "plus or minus" | any |
| `≈` | "approximately" | any |
| `–` (en dash) | "to" | **digit on both sides only** — never a hyphen |
| bare URL | "link" | matches a URL pattern |
| email address | removed | including the braced `{a,b}@host` form academic PDFs use |

Deliberately **not** expanded, per the milestone-2 decision: currency
magnitudes (`$5M`), general numerals, dates, versions, scores. Neural engines
read these correctly and expansion regresses more than it fixes. Each
candidate gets added only with a failing test that proves the engine needs it.

**C — spell out** (`Spell` nodes; allowlist only, never inferred from casing)

```
US USA UK EU UN  AI ML  API URL URI HTTP HTTPS SQL CSS HTML PDF XML JSON
CPU GPU RAM SSD USB OS IP DNS  CEO CFO CTO COO HR PR IT UI UX  MP MPs
PhD  NHS BBC ITV FBI CIA IRS FDA  GDP CPI  ID DNA RNA HIV  AGM CV
```

Casing alone is not a signal: `NASA`, `LASER`, `SCUBA`, `UNESCO` are spoken as
words. Before lookup, strip a trailing `'s` or `s` and re-attach it after —
`MPs` → `Spell("MP")` + `Say("s")`.

`WHO` is ambiguous (the organisation versus the pronoun) and is **excluded**;
it is read as written.

**D — protected patterns** (no rule in B or C may touch a match)

version strings (`v2.5`, `3.11.2`) · times (`3.30pm`, `14:05`) · file
extensions (`.pdf`) · domains and URLs · hyphenated compounds ·
scores (`3-2`) · identifiers containing digits and letters (`GPT-4o`,
`1810.04805`) · anything inside a `CODE` block.

**Not** protected: bare decimals such as `80.5`. Protecting every `\d+\.\d+`
matched version strings *and* ordinary numbers, which silently stopped the
percent rule firing — its lookbehind saw the mask, not a digit. The guard now
requires a `v` prefix, three components, or an arXiv-shaped id.

## A.5 Normalization rules, in order

Order matters — later rules assume earlier ones have run.

| # | Rule | Notes |
|---|---|---|
| 1 | Unicode hygiene | ligatures (`ﬁ`→`fi`), soft hyphens, zero-width chars, NBSP, smart quotes, emoji removed |
| 2 | PDF running headers/footers | a short block (≤ 12 words) whose digit-masked text repeats on ≥ 3 pages is dropped; being in the header/footer band lowers that to 2 pages |
| 2b | Preprint, DOI and licence stamps | narrow pattern match on short PDF blocks — **see the correction below** |
| 3 | Standalone page numbers | a block that is only digits/roman numerals with `position` > 0.85 |
| 4 | Line-break hyphen repair | `representa-\ntion` → `representation`, unless the word is hyphenated in a dictionary sense (`self-\nattention` keeps its hyphen) |
| 5 | Hard-wrap join | remaining newlines inside a PDF block become spaces |
| 6 | Boilerplate sweep | nav/cookie/share/bio/related/comment leftovers, by phrase list |
| 7 | Fuzzy duplicate removal | see below |
| 8 | Drop by kind | `CAPTION` (default on), `TABLE` (announced as "table omitted"), `CODE` (announced as "code omitted") |
| 9 | Citation markers | `[1]`, `[1,2]`, `¹` always; `(Smith 2019)` / `(Smith et al., 2019)` **PDF only, default off** |
| 10 | Protect D-patterns | mask before 11–12 run |
| 11 | Expansions (A.4-B) | |
| 12 | Spell-outs (A.4-C) | emits `Spell` nodes |
| 13 | Sentence-terminal punctuation | a heading or list item with no terminal punctuation gets a `.` so the engine does not run on |
| 14 | Spoken header | `Say(title)`, `Pause(SECTION)`, `Say("by " + author)` when known |
| 15 | Structure to pauses | headings, paragraphs and list items per the A.3 vocabulary |

**Rule 7 — fuzzy duplicate removal.** Exact matching does not work: pull
quotes are usually truncated versions of the sentence they quote, and a
standfirst is usually a reworded lead. The rule:

- Compare on lowercase alphanumeric token lists, stopwords kept.
- Drop block *B* if its token list is a contiguous subsequence of block *A*'s
  (the truncated-pull-quote case), or if `SequenceMatcher` ratio ≥ 0.85 and
  `len(B) ≤ 0.95 × len(A)`.
- Only `QUOTE`, `CAPTION` and paragraphs under 40 words are candidates for
  removal; a full body paragraph is never dropped as a duplicate.
- Comparison is document-wide. At these sizes the cost is irrelevant.

**Rule 15 — list handling.** Each `LIST_ITEM` becomes
`Say(text + terminal punctuation)` + `Pause(LIST_ITEM)`. Ordered lists speak
their ordinal as a word ("One.", "Two."); unordered lists speak no marker —
"bullet" read aloud thirty times is unbearable. A list of more than 12 items
is preceded by `Say("A list of N items.")`.

## A.6 New flags this appendix introduces

| Flag | Default | Behaviour |
|---|---|---|
| `--header` / `--no-header` | **on** | Speak "*title*, by *author*" before the body |
| `--keep-captions` | off | Do not apply rule 8 to captions |
| `--strip-citations` | off | Enable the `(Smith 2019)` half of rule 9 |

`--header` defaults to on because a folder of MP3s is navigable by ear only if
each one says what it is. Flip the default in one line if you disagree.

## A.7 Decisions log

| Decision | Rationale |
|---|---|
| Hosted default: **Azure Neural TTS** | Only engine with a stable, documented voice ID for all eight accents; $16/1M vs ElevenLabs' $100/1M on long-form; full SSML |
| Offline: **Piper, via subprocess** | Only offline option that does not pull torch. Subprocess, not import, keeps readaloud's MIT source clear of Piper's GPL-3.0 |
| **Every Piper voice's MODEL_CARD is checked before it enters `voices.yaml`** | Voice licences vary independently of the engine licence; some are non-commercial or share-alike |
| CLI: argparse, stdlib only | Each dependency is a Homebrew `resource` to maintain |
| trafilatura `deduplicate=False` | Its LRU is process-wide and silently empties a second extraction of the same text; our rule 7 does this properly |
| 403 retries once with a descriptive UA | Wikimedia blocks browser UAs and accepts an identifying one; publishers do the reverse |
| `--keep-text` writes the **normalized** text once the normalizer lands | What you read should be what the engine was given |

---

# Appendix B — change of direction (local models only)

*Sections 3 and 4 above describe a hosted default engine and an API-key chain.
That is superseded and left in place as the record of what was originally
asked for. Milestone 4 is now a local open-source model.*

## B.1 What was deleted

| Removed | Where it was |
|---|---|
| `Settings.api_key()` — the env → config-file credential chain | `config.py` |
| `MissingAPIKeyError` (exit code 9, now retired and not reused) | `errors.py` |
| `EngineSpec.env_var` / `.needs_key` | `engines/__init__.py` |
| The `azure` backend registration | `engines/__init__.py` |
| The `cost` row in `--estimate` | `cli.py` |
| `[engines.azure] api_key` and the credential note | config template |

Rate-limiting and exponential backoff were specified for milestone 5 and had
not been written, so there was nothing to delete — milestone 5 simply drops
them.

The per-chunk cache **stays**, rejustified: it now buys time rather than
money. Kokoro runs at roughly real time on CPU, so re-synthesizing a 30-minute
article after an interrupted run costs 30 minutes.

## B.2 Engines after the change

| Engine | Status |
|---|---|
| `kokoro` | The default. Kokoro-82M via onnxruntime. Milestone 4. |
| `say` | Development only, macOS. Already built. |
| `piper` | Milestone 6, or dropped — decided once Kokoro is working. |

### Milestone 5 carry-over

- Strip the site-name suffix from titles before the spoken header — Wikipedia
  pages currently announce themselves as "Isambard Kingdom Brunel - Wikipedia".
- The per-chunk cache, sized by the benchmark in B.4.

`voices.yaml` and the `TTSBackend` protocol are unchanged, as instructed.
`supports_ssml` stays on the protocol even though no local engine sets it —
the speech IR's `silence` pause style is what local engines use, and removing
the flag would bake "no engine ever has SSML" into the contract.

## B.3 The accent problem this creates

Kokoro's English is **American and British only**. Six of the eight accents in
section 1 have no voice.

**Decided:** an unsupported accent is a hard error, not a silent fallback —
`AccentUnavailableError`, its own exit code, naming the nearest available
accent and pointing at `--list-voices`. Unlike the gender fallback (where the
substitute is close enough that a warning suffices), an accent substitution
changes what the user is listening to for the entire run, and there's a real
alternative to offer instead of guessing on their behalf. All eight values
still parse in `--accent`; six of them raise.

## B.4 Synthesis speed: what actually moves it

The 1.3–1.9× real-time figure from the first Kokoro render prompted three
questions: does the CoreML execution provider help, does tuning ORT's
intra-op thread count help, and is there headroom in chunk-level parallelism
now that requests aren't rate-limited. Measured on the M1 Pro (8 cores) this
runs on, using `scripts/bench_synth.py`.

**CoreML: closed, structurally.** CoreML runs *slower* than plain CPU — about
0.65× the CPU speed, i.e. a third slower — because ONNX Runtime can only
place 1038 of the model's 2255 nodes on the CoreML provider. The graph splits
into 109 subgraphs, and the cost of crossing between CPU and CoreML at each
of those boundaries outweighs anything CoreML contributes on the nodes it
does run. This isn't a configuration problem to tune around; it's the shape
of the model export. Not revisiting unless a CoreML-native Kokoro export
appears.

**Chunk-level parallelism: no headroom, so not pursued further.** Setting
`intra_op_num_threads=1` — forcing ONNX Runtime down to one thread per
inference — cost 42% of throughput on its own. That's the size of the
parallelism ONNX Runtime already extracts from *one* inference call across
the 8 cores. Adding a worker pool across chunks (`intra_op=1` × 4 workers,
× 8 workers) never beat the single-threaded-default baseline, because the
workers are competing for the same 8 cores the default configuration was
already saturating. The single available lever — reducing per-call fixed
overhead by making fewer, larger calls — is a chunking decision, not a
threading one; see the chunk-length curve below.

**A measurement artifact worth disclosing.** The 1.3–1.9× figure came from
`compare_voices.py`, which very likely ran concurrently with a 172-second
full pytest run in the same session — and a single Kokoro inference already
saturates all 8 cores (see above), so any concurrent CPU-bound work would
directly slow it down. That can't be proven after the fact, but every clean,
single-process measurement taken since — 28 individual calibration calls, an
isolated wrapper-overhead check, and the chunk-length curve below — lands
consistently at **0.36–0.49× real time (faster than real time)**, not
1.3–1.9×. The wrapper around the raw model call (the truncation guard,
WAV encoding) was checked directly and adds no measurable overhead (both
landed at 0.40–0.44×RT on the same six-run sample). Treat 1.3–1.9× as an
artifact of that specific run, not Kokoro's real speed on an uncontended
machine.

**Chunk-length curve.** `scripts/chunk_length_curve.py` measures speedup
against chunk size for one fixed passage (the Brunel Wikipedia article, 700
words, 10 spoken runs, 9 A.3 pause boundaries), chunked *across* those pause
boundaries the way a naive "bigger chunks, fewer calls" change would, and
separately counts what fraction of the original pauses survive at each size
— a pause only exists as stitch-time silence, so one absorbed into the
middle of a merged chunk is gone, not delayed, not shortened.

| chunk size | calls | wall | xRT | vs. one-call-per-run | pauses kept |
|---|---|---|---|---|---|
| (baseline: one call per run) | 10 | 93.2s | 0.36 | 1.00× | 9/9 (100%) |
| 100 chars | 26 | 116.0s | 0.45 | 0.80× | 7/9 (78%) |
| 250 chars | 24 | 103.0s | 0.40 | 0.90× | 7/9 (78%) |
| 500 chars | 14 | 118.2s | 0.46 | 0.79× | 7/9 (78%) |
| 1000 chars | 6 | 118.2s | 0.46 | 0.79× | 5/9 (56%) |
| 2000 chars | 3 | 124.4s | 0.49 | 0.75× | 2/9 (22%) |

**Merging is not just pause-lossy, it is also slower, at every size tested.**
Fewer, larger calls were expected to win by amortizing fixed per-call
overhead; instead every merged configuration lost 10–25% of the baseline's
speed while destroying up to 78% of the A.3 pauses. The likely reason:
`intra_op` parallelism (§B.4 above) means the "fixed per-call overhead" this
was meant to amortize was never large relative to a big multi-core inference
in the first place, so there was nothing worth amortizing — merging only
pays the cost of synthesizing more text per call (which doesn't get
faster) while forfeiting the fine-grained, silence-based pause mechanism
that costs nothing extra.

**Acted on.** Milestone 5's chunker (`chunk.py`) keeps this shape: one
synthesize() call per spoken run, with an oversized run split at *sentence*
boundaries only, per A.3 rule 4's clause-boundary/word-boundary fallback.
Every A.3 Pause is a hard chunk boundary unconditionally — not just because
it measured faster, but because for `pause_style == "silence"` (every local
engine today) it is a correctness requirement: render() emits nothing for a
Pause under that style, so a chunk spanning one would silently drop it, not
merely leave it un-embedded. See A.3 rule 3, which already specified stitch-
time silence "regardless of pause_style" — this chunker is the literal
implementation of that rule, not a new policy.

## B.5 Milestone 5 — chunk, cache, stitch, tag

Built against Appendix A.3 and the B.4 measurement above.

- **`chunk.py`** — `chunk_speech()` splits at Pause boundaries (rule 1);
  `chunk_run()` packs multiple sentences into one call when a run must be
  split at all (crosses no A.3 pause, since Pauses.SENTENCE = 0 — costs
  nothing the curve measured merging as costing); `_split_oversized_sentence()`
  implements rule 4's clause-boundary-then-word-boundary fallback, flagged
  via `reporter.stage()` ("flagged in --verbose", per the contract).
  Sentence boundaries are found using `abbreviations.ends_sentence()` — the
  same function the normalizer's own sentence-terminal-punctuation rule
  uses (A.5 rule 13) — so the two never disagree about where a sentence ends.

- **Concurrency: sequential, deliberately, against SPEC.md section 2's
  original "bounded worker pool" language.** That text was written for a
  rate-limited hosted engine; scripts/bench_synth.py measured a worker pool
  regressing Kokoro's throughput at every configuration, because a single
  inference already saturates all 8 cores. `_synthesize_chunks()` is a plain
  sequential loop over pure per-chunk work, structured so a future backend
  that genuinely benefits from concurrency doesn't require restructuring —
  just a worker pool dropped in around the same loop body.

- **Cache (`cache.py`)** — `hash(text + voice + speed + engine)`, exactly
  as specced. Writes are atomic (temp file + rename), which fetch.py's
  cache is not: a truncated *webpage* cache entry just gets refetched; a
  truncated *audio* entry read as complete would splice broken audio into
  an otherwise good file, silently. Verified against a real `kill -9`
  mid-synthesis: 6 of 24 chunks survived intact with no `.tmp` leftovers,
  and the re-run reused all 6 (`[cache] 6/24 chunks reused`) and produced a
  clean, fully-decodable file.

- **Stitch** — one `ffmpeg` call: concat demuxer (chunk WAVs interleaved
  with generated silence files for each `pause_after_ms`), single-pass
  `loudnorm=I=-16:TP=-1.5:LRA=11`, `-metadata` tags, final codec. Every
  chunk is first decoded to a fixed 24kHz mono PCM WAV regardless of the
  backend's native container (WAV for Kokoro, AIFF for `say`), so chunks
  from any backend and the silence this module generates always share
  identical parameters and concatenate cleanly.

- **Loudnorm can crash on near-silent-throughout audio.** Discovered via
  the test suite's FakeBackend (pure digital silence, on purpose, for fast
  offline tests) tripping `libmp3lame`'s psychoacoustic model: normalizing
  integrated loudness that is at or near -inf LUFS asks for an
  undefined/near-infinite gain, which some `ffmpeg`/LAME builds cannot
  encode without an internal assertion. The `_check_output` guard in
  engines/kokoro.py already rejects empty/non-finite/implausibly-short
  chunk audio before it reaches here, so real Kokoro speech should never
  legitimately trigger this — but a real short document dominated by
  inserted A.3 pause silence relative to very little speech (a preview of
  mostly headings, say) could plausibly land in the same shape. `_stitch()`
  retries once without `-af loudnorm=...` and warns, rather than failing
  the whole run over a cosmetic normalization pass.

- **Tag** — title, artist, album (`readaloud`), comment (source URL or
  path), via ffmpeg's own `-metadata` flags. No new dependency.

- **Title site-suffix strip** (the carried-over item from B.2) —
  `extract/html.py`'s `_strip_site_suffix()`. Conservative by design: only
  strips the rightmost recognised separator's trailing segment, and only
  when that segment matches the page's *own* URL domain — ground truth,
  not a guess from the segment's length or shape. A fuzzy substring
  fallback was tried and rejected during review: "newyork" is a substring
  of "newyorker", so a real title like "Best Restaurants - New York" on
  newyorker.com would have had "New York" mistaken for the site name and
  deleted. Exact match only; under-stripping a verbose site name is judged
  a smaller cost than over-stripping real content.

- **Non-TTY progress was silent** for a multi-minute synthesis before this
  milestone — `Reporter.progress()` only drew the TTY bar; a piped/redirected
  run got nothing until the final line. Section 1's "no progress bar when
  piped" and "silence is unacceptable" are only in tension if "progress bar"
  means any output at all; fixed to mean specifically the `\r`-rewriting bar.
  A redirected run now gets a plain line roughly every 5% of the way through.

**Verified for real**, not just under test: a 985-word Guardian article
end to end (24 chunks, correct tags, clean `ffmpeg -f null` decode, 0.48×
real time uncontended); a full `kill -9` mid-synthesis and resume; cache
reuse dropping a 28.5s run to 4.1s on identical input; `--estimate` at
1.08s making zero synthesis calls.

## B.6 Milestone 6 — second engine decision, voices.toml

B.2 left Piper as "milestone 6, or dropped — decided once Kokoro is
working." Kokoro is not just working now but shipped (milestone 7's
Homebrew formula passes `brew audit --strict --online --new`, builds from
source, and its `test do` synthesizes real audio), so this is that
decision, made against Piper's *current* state rather than the state
assumed when B.2 was written — per this project's own standing rule not to
trust training data over a live check.

**Piper, checked now:** the original `rhasspy/piper` — MIT-licensed, which
is what B.2's "subprocess, not import" reasoning was protecting readaloud's
own MIT source against — was archived and made read-only in October 2025.
Active development moved to `OHF-Voice/piper1-gpl`, and the name is
accurate: it really is GPL-3.0-or-later now, the license B.2 was written
expecting. So the licensing shape B.2 anticipated is real, not stale.

**Decided: drop it, not build it.** Two independent findings, not one:

- **It would not close the accent gap.** B.3's whole complaint is that
  Kokoro covers American and British English only. Piper's actual voice
  set (checked against its Hugging Face voice repository) is also `en_US`
  and `en_GB` only — nothing for `au`, `ie`, `in`, `za`, `nz`, or `ca`. A
  second engine with the *same* two accents buys no coverage; the only
  thing it would add is an alternate voice character in voices already
  served.
- **Packaging it cleanly costs more than that's worth.** Piper's own
  dependencies are lean (`onnxruntime`, `pathvalidate` — no torch), so
  weight isn't the blocker section 7 originally worried about. The
  blocker is licensing hygiene: a `resource` block installs straight into
  readaloud's own Homebrew Cellar prefix alongside every MIT-licensed
  dependency, which is a much closer coupling than "subprocess, not
  import" was meant to allow. Doing it properly — a separate
  `depends_on "piper-tts"` pointing at its own formula, isolated in its
  own prefix — isn't available off the shelf (homebrew-core has no
  `piper` or `piper-tts` formula), so it would mean writing, publishing,
  and maintaining a *second* personal tap formula, for a dependency that
  is also flagged as looking for maintainers.

Net: a second offline engine here would be more to isolate and maintain
for zero net accent coverage. `say` remains what it always was —
development-only, unshipped. Two stale references to a Piper that was
never built are fixed alongside this: `say.py`'s missing-binary hint
suggested `--engine piper`; a config test used `"piper"` as its example
engine string. Both now point at/use something real (`kokoro`).

This is a decision about *this* engine, not the accent gap itself — if a
maintained offline TTS project ever ships genuine `au`/`ie`/`in`/`za`/
`nz`/`ca` English, it's worth a fresh look; nothing here forecloses that.

**`voices.yaml` → `voices.toml`.** SPEC.md section 3 calls for a
`voices.yaml` mapping `(engine, accent, gender)` → voice ID as data, not
code — kokoro.py's `VOICE_TABLE`/`SUPPORTED_ACCENTS`/`NEAREST_ACCENT`/
`ACCENT_NAMES` constants were exactly that data, just left in Python
pending this milestone (see the comment that was on `VOICE_TABLE`). Real
YAML needs PyYAML, a new Homebrew resource, for one small config file;
`tomllib` is stdlib (3.11+, this project targets 3.12+) and equally
editable at this size — comments, nested tables, ordered lists of
records — so the table now lives in `src/readaloud/voices.toml`, loaded
through the new `readaloud/voices.py`. This is the same "stdlib where it
genuinely covers the need" bias as the CLI's own argparse decision (A.7),
applied to a second dependency this project was about to add. `say.py`
keeps its own dynamic, subprocess-discovered voice list rather than
moving into the table — those voices are whatever the local machine has
installed, so a static table would go stale by construction.

`--list-voices` and accent/gender selection needed no new wiring: both
were already engine-generic from milestone 4/5 —
`synthesize.resolve_voice()` duck-types on `backend.resolve_voice()`, and
`cli.list_voices()` groups whatever `backend.list_voices()` returns. The
only genuinely hardcoded thing was kokoro's own table; moving it to data
was the actual scope of this milestone.

**Verified for real:** the full suite (319 tests, 1 skipped pending the
model download) passes with the table moved; a real wheel was built with
`python -m build` and installed into a clean venv — not the source tree,
and run from `/tmp` rather than the repo — to confirm `voices.toml` is
actually inside the wheel and `importlib.resources` finds it after
install, not just under an editable install where that would be
unrepresentative; and the CLI end to end
(`readaloud article.html --engine kokoro --accent au --gender female`)
reproduces the exact `AccentUnavailableError` (exit code 12, naming
`--accent uk`) sourced now from `voices.toml` rather than Python.
