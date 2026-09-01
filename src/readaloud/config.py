"""Config file loading and option resolution.

Precedence, highest first:

    command-line flag  >  environment variable  >  config file  >  built-in default

Every backend is local, so there are no credentials anywhere in this module.

The file lives at `~/.config/readaloud/config.toml` (or `$XDG_CONFIG_HOME`).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from readaloud.errors import ConfigError

APP_NAME = "readaloud"

ACCENTS = ("us", "uk", "au", "ie", "in", "za", "nz", "ca")
GENDERS = ("female", "male")
FORMATS = ("mp3", "m4a", "wav")

SPEED_MIN = 0.5
SPEED_MAX = 2.0

DEFAULT_ENGINE = "kokoro"
DEFAULT_ACCENT = "uk"
DEFAULT_GENDER = "female"
DEFAULT_FORMAT = "mp3"
DEFAULT_SPEED = 1.0

# Environment overrides for the [defaults] table.
_ENV_PREFIX = "READALOUD_"

CONFIG_TEMPLATE = """\
# readaloud configuration
#
# Anything here can be overridden by a command-line flag.
# Every engine runs locally; there are no credentials to set.

[defaults]
# engine = "kokoro"       # local neural TTS — the default, no network
# engine = "say"          # macOS `say`, development only
accent = "uk"             # us, uk, au, ie, in, za, nz, ca
gender = "female"         # female, male
format = "mp3"            # mp3, m4a, wav
speed = 1.0               # 0.5 – 2.0
# header = true           # speak "<title>, by <author>" before the body

[engines.kokoro]
# model = "full"          # full is the default; fp16 and q8f16 are
                          # smaller but emit silent NaN audio
"""


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / APP_NAME


def cache_home() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / APP_NAME


def config_path() -> Path:
    return config_home() / "config.toml"


@dataclass(frozen=True)
class Settings:
    """Fully resolved options for one run."""

    engine: str = DEFAULT_ENGINE
    accent: str = DEFAULT_ACCENT
    gender: str = DEFAULT_GENDER
    voice: str | None = None
    speed: float = DEFAULT_SPEED
    format: str = DEFAULT_FORMAT
    header: bool = True
    engine_options: dict[str, dict[str, Any]] = None  # type: ignore[assignment]
    source_path: Path | None = None

    def __post_init__(self) -> None:
        if self.engine_options is None:
            object.__setattr__(self, "engine_options", {})

    def options_for(self, engine: str) -> dict[str, Any]:
        return dict(self.engine_options.get(engine, {}))


def _validate_choice(name: str, value: Any, allowed: tuple[str, ...], origin: str) -> str:
    if not isinstance(value, str) or value.lower() not in allowed:
        raise ConfigError(
            f"invalid {name} {value!r} in {origin}",
            f"Valid values: {', '.join(allowed)}.",
        )
    return value.lower()


def _validate_speed(value: Any, origin: str) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"invalid speed {value!r} in {origin}",
            f"Speed is a number between {SPEED_MIN} and {SPEED_MAX}.",
        ) from exc
    if not SPEED_MIN <= speed <= SPEED_MAX:
        raise ConfigError(
            f"speed {speed} in {origin} is out of range",
            f"Speed must be between {SPEED_MIN} and {SPEED_MAX}.",
        )
    return speed


def _validate_bool(value: Any, origin: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "yes", "no", "1", "0"):
        return value.lower() in ("true", "yes", "1")
    raise ConfigError(
        f"invalid boolean {value!r} in {origin}",
        "Use true or false.",
    )


def read_file(path: Path | None = None) -> dict[str, Any]:
    """Parse the config file. Returns {} when there isn't one."""
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid TOML: {exc}",
            "Fix the file, or run `readaloud config` to edit it.",
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"could not read {path}: {exc.strerror or exc}",
            "Check the file's permissions.",
        ) from exc


def _from_env() -> dict[str, Any]:
    """Pick up READALOUD_* overrides for the [defaults] table."""
    found: dict[str, Any] = {}
    for field in ("engine", "accent", "gender", "voice", "speed", "format"):
        value = os.environ.get(f"{_ENV_PREFIX}{field.upper()}")
        if value is not None and value.strip():
            found[field] = value.strip()
    return found


def load(path: Path | None = None) -> Settings:
    """Build Settings from the config file and the environment (no CLI yet)."""
    path = path or config_path()
    document = read_file(path)

    defaults = document.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError(
            f"[defaults] in {path} must be a table",
            "Run `readaloud config` and compare against the commented template.",
        )

    engines = document.get("engines", {})
    if not isinstance(engines, dict):
        raise ConfigError(
            f"[engines] in {path} must be a table",
            "Each engine gets its own table, e.g. [engines.kokoro].",
        )
    engine_options = {
        name: dict(table) for name, table in engines.items() if isinstance(table, dict)
    }

    merged: dict[str, Any] = {**defaults, **_from_env()}
    origin = f"{path}, or a READALOUD_* environment variable"

    settings = Settings(engine_options=engine_options)
    if "engine" in merged:
        engine = merged["engine"]
        if not isinstance(engine, str) or not engine.strip():
            raise ConfigError(f"invalid engine {engine!r} in {origin}", "Use an engine name.")
        settings = replace(settings, engine=engine.strip().lower())
    if "accent" in merged:
        settings = replace(settings, accent=_validate_choice("accent", merged["accent"], ACCENTS, origin))
    if "gender" in merged:
        settings = replace(settings, gender=_validate_choice("gender", merged["gender"], GENDERS, origin))
    if "format" in merged:
        settings = replace(settings, format=_validate_choice("format", merged["format"], FORMATS, origin))
    if "speed" in merged:
        settings = replace(settings, speed=_validate_speed(merged["speed"], origin))
    if merged.get("voice"):
        settings = replace(settings, voice=str(merged["voice"]))
    if "header" in merged:
        settings = replace(settings, header=_validate_bool(merged["header"], origin))

    return replace(settings, source_path=path if path.exists() else None)


def apply_cli(settings: Settings, args: Any) -> Settings:
    """Overlay parsed command-line arguments. Flags win over everything else."""
    updates: dict[str, Any] = {}
    for field in ("engine", "accent", "gender", "voice", "format"):
        value = getattr(args, field, None)
        if value is not None:
            updates[field] = value.lower() if field != "voice" else value
    if getattr(args, "speed", None) is not None:
        updates["speed"] = _validate_speed(args.speed, "--speed")
    if getattr(args, "header", None) is not None:
        updates["header"] = bool(args.header)
    return replace(settings, **updates)


def ensure_file(path: Path | None = None) -> Path:
    """Create the config file from the template if it doesn't exist yet."""
    path = path or config_path()
    if path.exists():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        # It can hold an API key, so keep it owner-readable only.
        path.chmod(0o600)
    except OSError as exc:
        raise ConfigError(
            f"could not create {path}: {exc.strerror or exc}",
            "Check that the parent directory is writable.",
        ) from exc
    return path
