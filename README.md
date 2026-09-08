# readaloud

[![tests](https://github.com/Nornchan/readaloud/actions/workflows/tests.yml/badge.svg)](https://github.com/Nornchan/readaloud/actions/workflows/tests.yml)
[![homebrew-readaloud CI](https://github.com/Nornchan/homebrew-readaloud/actions/workflows/tests.yml/badge.svg)](https://github.com/Nornchan/homebrew-readaloud/actions/workflows/tests.yml)

Turn a web article, HTML file, or PDF into a listenable audio file, with a
choice of English accent and voice gender.

```
readaloud https://example.com/article
readaloud paper.pdf --accent us --gender male
readaloud notes.html -o notes.m4a -f m4a
```

## Status

Built in milestones (see `SPEC.md`, including Appendices A and B — the
pipeline contract and the change to local-only engines). All seven are
done: skeleton (1), extraction (2), normalization (3), first synthesis
(4), chunking + caching + stitching + tagging (5), `voices.toml` +
accent/gender selection (6), and the Homebrew formula (7). `readaloud
<source>` produces real, tagged, loudness-normalized audio end to end via
the local Kokoro-82M engine. A second offline engine (Piper) was
evaluated for milestone 6 and deliberately dropped — see Appendix B.6.

## Install

```bash
brew install Nornchan/readaloud/readaloud
```

Apple Silicon only — the local Kokoro engine runs on onnxruntime, which
ships no Intel-macOS build. `ffmpeg` and `python@3.12` come along as
dependencies. On first use, readaloud downloads Kokoro's model (~310MB)
and caches it in `~/.cache/readaloud/models`; after that it runs fully
offline.

## Install (development)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Configuration

`~/.config/readaloud/config.toml` (or `$XDG_CONFIG_HOME/readaloud/config.toml`).
`readaloud config` creates it from a commented template and opens `$EDITOR`.

Options resolve in this order, highest first:

    flag  >  READALOUD_* environment variable  >  config file  >  built-in default

API keys are read from the environment first and the config file second. There
is deliberately no flag for them — a flag would leak the key into shell history.

## Tests

```bash
.venv/bin/pytest
```
