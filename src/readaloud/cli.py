"""Command-line interface.

argparse rather than click/typer on purpose: the Homebrew formula turns every
runtime dependency into a `resource` block that has to be maintained by hand,
and the argument surface here is small enough that the stdlib covers it.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from readaloud import __version__, config, engines, output, speech as speech_ir
from readaloud.config import ACCENTS, FORMATS, GENDERS, SPEED_MAX, SPEED_MIN, Settings
from readaloud.document import Document
from readaloud.errors import ConfigError, ReadaloudError, UsageError
from readaloud.extract import extract
from readaloud.extract.html import paywall_markers
from readaloud.normalize import NormalizeOptions, normalize
from readaloud.source import Source, SourceKind, detect
from readaloud.synthesize import format_duration, play, synthesize_once
from readaloud.terminal import Reporter

# Roughly how fast a neural TTS voice speaks at 1.0x. Used only for the
# runtime figure in --estimate.
WORDS_PER_MINUTE = 150.0

# A URL that yields less than this is almost certainly truncated — by a
# paywall, by a consent wall, or by a page that renders its body in
# JavaScript. Catching it by word count catches all three, which guessing at
# paywall markup did not.
THIN_ARTICLE_WORDS = 400

PREVIEW_WORDS = 150

PROG = "readaloud"

_EPILOG = f"""\
examples:
  readaloud https://example.com/article
  readaloud paper.pdf --accent us --gender male
  readaloud notes.html -o notes.m4a -f m4a
  readaloud https://example.com/article --estimate
  readaloud --list-voices

environment:
  READALOUD_ENGINE     default engine, overriding the config file
  READALOUD_ACCENT     default accent   (also _GENDER, _VOICE, _SPEED, _FORMAT)
  NO_COLOR             disable coloured output

config:
  {config.config_path()}
  `readaloud config` opens it in $EDITOR.
"""


def _speed(value: str) -> float:
    try:
        speed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not SPEED_MIN <= speed <= SPEED_MAX:
        raise argparse.ArgumentTypeError(
            f"speed must be between {SPEED_MIN} and {SPEED_MAX} (got {speed})"
        )
    return speed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Turn a web article, HTML file, or PDF into a listenable audio file.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        nargs="?",
        metavar="SOURCE",
        help="a URL, a local .html file, or a local .pdf file",
    )
    parser.add_argument(
        "-a", "--accent",
        choices=ACCENTS,
        help=f"English accent (default: {config.DEFAULT_ACCENT})",
    )
    parser.add_argument(
        "-g", "--gender",
        choices=GENDERS,
        help=f"voice gender (default: {config.DEFAULT_GENDER})",
    )
    parser.add_argument(
        "-v", "--voice",
        metavar="ID",
        help="exact voice ID; overrides --accent and --gender",
    )
    parser.add_argument(
        "-e", "--engine",
        choices=engines.engine_names(),
        help="TTS backend (default: from config)",
    )
    parser.add_argument(
        "--speed",
        type=_speed,
        metavar="RATE",
        help=f"speaking rate, {SPEED_MIN}–{SPEED_MAX} (default: {config.DEFAULT_SPEED})",
    )
    parser.add_argument(
        "-o", "--out",
        metavar="PATH",
        type=Path,
        help="output path (default: derived from the article title)",
    )
    parser.add_argument(
        "-f", "--format",
        choices=FORMATS,
        help=f"audio format (default: {config.DEFAULT_FORMAT})",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="print the active engine's voices, grouped by accent, and exit",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="synthesize and play only the first ~150 words, then exit",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="print word count, character count and runtime; synthesize nothing",
    )
    parser.add_argument(
        "--keep-text",
        action="store_true",
        help="write the spoken text next to the audio file",
    )
    parser.add_argument(
        "--header",
        dest="header",
        action="store_true",
        default=None,
        help="speak the title and author before the body (default)",
    )
    parser.add_argument(
        "--no-header",
        dest="header",
        action="store_false",
        help="start straight into the body",
    )
    parser.add_argument(
        "--keep-captions",
        action="store_true",
        help="read image and figure captions instead of dropping them",
    )
    parser.add_argument(
        "--strip-citations",
        action="store_true",
        help="also remove (Author 2019) citations from PDFs",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="bypass the cache",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show each pipeline stage on stderr",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
    )
    return parser


def _check_arguments(args: argparse.Namespace) -> None:
    """Reject argument combinations argparse can't express on its own."""
    if args.preview and args.estimate:
        raise UsageError(
            "--preview and --estimate cannot be used together",
            "--estimate reports without synthesizing; --preview synthesizes a sample.",
        )
    if args.list_voices and args.source:
        raise UsageError(
            "--list-voices does not take a source",
            "Run `readaloud --list-voices` on its own, or drop the flag.",
        )
    if not args.list_voices and not args.source:
        raise UsageError(
            "no source given",
            "Pass a URL, an .html file, or a .pdf file. See `readaloud --help`.",
        )


def resolve(args: argparse.Namespace) -> Settings:
    """Config file, then environment, then flags."""
    return config.apply_cli(config.load(), args)


def open_config(reporter: Reporter) -> int:
    """`readaloud config` — create the file if needed and open $EDITOR."""
    path = config.ensure_file()

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        editor = "vi" if shutil.which("vi") else ""

    if not editor or not sys.stdin.isatty():
        # No editor, or nowhere to run one — the path is still the useful answer.
        print(path)
        return 0

    command = shlex.split(editor) + [str(path)]
    if not shutil.which(command[0]):
        raise ConfigError(
            f"editor {command[0]!r} not found",
            f"Set $EDITOR to something on your PATH, or edit {path} directly.",
        )
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        raise ConfigError(
            f"could not run {command[0]!r}: {exc.strerror or exc}",
            f"Edit {path} directly instead.",
        ) from exc
    return completed.returncode


def list_voices(settings: Settings, reporter: Reporter) -> int:
    reporter.stage("engine", settings.engine)
    backend = engines.load(settings.engine)  # raises until milestone 6
    grouped: dict[str, list] = {}
    for voice in backend.list_voices():
        grouped.setdefault(voice.accent or "other", []).append(voice)
    for accent in sorted(grouped):
        print(accent)
        for voice in sorted(grouped[accent], key=lambda entry: entry.name):
            gender = voice.gender or "-"
            print(f"  {voice.id:<28} {voice.name:<24} {gender}")
    return 0


def print_estimate(
    document: Document, speech: speech_ir.Speech, settings: Settings
) -> int:
    """--estimate: counts and runtime, with no synthesis calls made."""
    minutes = speech_ir.duration_estimate(speech, WORDS_PER_MINUTE, settings.speed)

    rows = [
        ("words", f"{speech_ir.word_count(speech):,}"),
        ("characters", f"{speech_ir.char_count(speech):,}"),
        ("extracted", f"{document.word_count():,} words in {len(document.blocks):,} blocks"),
    ]
    if document.page_count:
        rows.append(("pages", f"{document.page_count:,}"))
    rows.append(("runtime", f"~{minutes:.0f} min at {settings.speed}x"))

    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label:<{width}}  {value}")
    return 0


def report_extraction(
    document: Document, source: Source, args: argparse.Namespace, reporter: Reporter
) -> None:
    """Always say how much text came out, and shout when it looks truncated."""
    words = document.word_count()
    reporter.note(f"{words:,} words extracted")

    if source.is_url and words < THIN_ARTICLE_WORDS:
        raw_html = _raw_html(source, args)
        detail = ""
        if raw_html and paywall_markers(raw_html):
            detail = " The page carries paywall markup."
        reporter.warn(
            f"only {words:,} words came out of this URL, which is short for an "
            f"article — it may be truncated, gated, or rendered by JavaScript."
            f"{detail} Check --keep-text before spending on synthesis."
        )


def write_text(speech: speech_ir.Speech, path: Path, reporter: Reporter) -> None:
    """--keep-text writes the *spoken* text, so what you read is what was sent."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(speech_ir.transcript(speech), encoding="utf-8")
    except OSError as exc:
        raise ReadaloudError(
            f"could not write {path}: {exc.strerror or exc}",
            "Check that the directory exists and is writable.",
        ) from exc
    reporter.stage("keep-text", str(path))


def run(args: argparse.Namespace, settings: Settings, reporter: Reporter) -> int:
    """The main pipeline. Extraction is wired up; synthesis follows."""
    source: Source = detect(args.source)

    reporter.stage("source", source.describe())
    reporter.stage("engine", settings.engine)
    reporter.stage("voice", settings.voice or f"{settings.accent}/{settings.gender}")
    reporter.stage("speed", f"{settings.speed}x")
    reporter.stage("format", settings.format)
    reporter.stage("cache", "bypassed" if args.no_cache else str(config.cache_home()))
    if settings.source_path:
        reporter.stage("config", str(settings.source_path))

    document = extract(source, use_cache=not args.no_cache, reporter=reporter)
    report_extraction(document, source, args, reporter)

    speech = normalize(
        document,
        NormalizeOptions(
            header=settings.header,
            keep_captions=args.keep_captions,
            strip_citations=args.strip_citations,
            is_pdf=source.kind is SourceKind.PDF,
        ),
    )
    reporter.stage(
        "normalize",
        f"{len(speech)} nodes, {speech_ir.word_count(speech):,} spoken words",
    )

    if args.estimate:
        return print_estimate(document, speech, settings)

    audio = output.audio_path(document, settings.format, args.out)

    if args.keep_text:
        write_text(speech, output.text_path(audio), reporter)

    if args.preview:
        speech = speech_ir.head(speech, PREVIEW_WORDS)
        audio = audio.with_name(f"{audio.stem}-preview{audio.suffix}")
        reporter.stage("preview", f"first {PREVIEW_WORDS} words")

    try:
        backend = engines.load(settings.engine)
    except ReadaloudError:
        if args.keep_text:
            print(output.text_path(audio))
            reporter.warn(
                f"wrote the text only — the {settings.engine} backend is not "
                "built yet. Try --engine say to hear it."
            )
            return 0
        raise

    path, seconds = synthesize_once(speech, backend, settings, audio, reporter)

    if args.preview:
        play(path)
        return 0

    print(f"{path}  {format_duration(seconds)}")
    return 0


def _raw_html(source: Source, args: argparse.Namespace) -> str | None:
    """The fetched page, for the paywall-markup detail on a thin-article warning."""
    if not source.is_url:
        return None
    from readaloud.fetch import fetch

    try:
        response = fetch(source.url, use_cache=not args.no_cache)
    except ReadaloudError:
        return None
    return None if response.is_pdf else response.text()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # `readaloud config` is a subcommand, not a source. Checked before parsing
    # so it doesn't have to fight the positional argument.
    if argv and argv[0] == "config":
        reporter = Reporter()
        if len(argv) > 1:
            reporter.report(
                UsageError(
                    f"`{PROG} config` takes no arguments",
                    "It opens the config file in $EDITOR.",
                )
            )
            return UsageError.exit_code
        try:
            return open_config(reporter)
        except ReadaloudError as exc:
            return reporter.report(exc)

    parser = build_parser()
    args = parser.parse_args(argv)
    reporter = Reporter(verbose=args.verbose)

    try:
        _check_arguments(args)
        settings = resolve(args)
        if args.list_voices:
            return list_voices(settings, reporter)
        return run(args, settings, reporter)
    except ReadaloudError as exc:
        return reporter.report(exc)
    except KeyboardInterrupt:
        reporter.error("interrupted")
        return 130
    except BrokenPipeError:  # pragma: no cover - `readaloud … | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141


def entrypoint() -> None:
    sys.exit(main())
