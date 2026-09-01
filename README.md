# readaloud

Turn a web article, HTML file, or PDF into a listenable audio file, with a
choice of English accent and voice gender.

```
readaloud https://example.com/article
readaloud paper.pdf --accent us --gender male
readaloud notes.html -o notes.m4a -f m4a
```

## Status

Built in milestones (see `SPEC.md`). Milestone 1 — CLI, config, source-type
detection — is done. Extraction, normalization, and synthesis follow.

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
