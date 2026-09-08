import argparse


import pytest

from readaloud import config
from readaloud.errors import ConfigError


def write_config(text: str):
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def args(**kwargs):
    defaults = dict(engine=None, accent=None, gender=None, voice=None, speed=None, format=None)
    return argparse.Namespace(**{**defaults, **kwargs})


def test_defaults_with_no_config_file():
    settings = config.load()
    assert settings.engine == config.DEFAULT_ENGINE
    assert settings.accent == "uk"
    assert settings.gender == "female"
    assert settings.format == "mp3"
    assert settings.speed == 1.0
    assert settings.voice is None
    assert settings.source_path is None


def test_paths_follow_xdg(tmp_path):
    assert config.config_path() == tmp_path / "config" / "readaloud" / "config.toml"
    assert config.cache_home() == tmp_path / "cache" / "readaloud"


def test_config_file_values():
    # Any string round-trips here; validating it against the engine registry
    # happens later, at engines.load() time.
    write_config('[defaults]\nengine = "example-engine"\naccent = "in"\ngender = "male"\nspeed = 1.25\n')
    settings = config.load()
    assert (settings.engine, settings.accent, settings.gender, settings.speed) == (
        "example-engine", "in", "male", 1.25,
    )
    assert settings.source_path is not None


def test_environment_beats_config_file(monkeypatch):
    write_config('[defaults]\naccent = "uk"\n')
    monkeypatch.setenv("READALOUD_ACCENT", "au")
    assert config.load().accent == "au"


def test_flag_beats_environment(monkeypatch):
    write_config('[defaults]\naccent = "uk"\n')
    monkeypatch.setenv("READALOUD_ACCENT", "au")
    settings = config.apply_cli(config.load(), args(accent="ca"))
    assert settings.accent == "ca"


def test_flags_leave_unset_fields_alone():
    write_config('[defaults]\ngender = "male"\n')
    settings = config.apply_cli(config.load(), args(accent="us"))
    assert settings.accent == "us"
    assert settings.gender == "male"


def test_invalid_accent_in_file_names_the_valid_ones():
    write_config('[defaults]\naccent = "martian"\n')
    with pytest.raises(ConfigError, match="invalid accent"):
        config.load()


def test_speed_out_of_range_in_file():
    write_config("[defaults]\nspeed = 9\n")
    with pytest.raises(ConfigError, match="out of range"):
        config.load()


def test_speed_out_of_range_from_flag():
    with pytest.raises(ConfigError, match="out of range"):
        config.apply_cli(config.load(), args(speed=3.0))


def test_malformed_toml():
    write_config("[defaults\nengine = broken")
    with pytest.raises(ConfigError, match="not valid TOML"):
        config.load()


def test_defaults_must_be_a_table():
    write_config('defaults = "nope"\n')
    with pytest.raises(ConfigError, match=r"\[defaults\].*must be a table"):
        config.load()


def test_engine_options_are_exposed():
    write_config('[engines.kokoro]\nmodel = "q8f16"\n')
    assert config.load().options_for("kokoro") == {"model": "q8f16"}


def test_there_is_no_credential_lookup_left():
    """Every backend is local; a key chain would be dead code and a footgun."""
    assert not hasattr(config.Settings(), "api_key")


def test_ensure_file_creates_a_private_template():
    path = config.ensure_file()
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "[defaults]" in path.read_text()


def test_ensure_file_does_not_clobber():
    write_config('[defaults]\naccent = "za"\n')
    assert 'accent = "za"' in config.ensure_file().read_text()
