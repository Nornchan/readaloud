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
    write_config('[defaults]\nengine = "piper"\naccent = "in"\ngender = "male"\nspeed = 1.25\n')
    settings = config.load()
    assert (settings.engine, settings.accent, settings.gender, settings.speed) == (
        "piper", "in", "male", 1.25,
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


def test_api_key_from_environment(monkeypatch):
    write_config('[engines.elevenlabs]\napi_key = "from-file"\n')
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-env")
    assert config.load().api_key("elevenlabs", "ELEVENLABS_API_KEY") == "from-env"


def test_api_key_falls_back_to_config_file():
    write_config('[engines.elevenlabs]\napi_key = "from-file"\n')
    assert config.load().api_key("elevenlabs", "ELEVENLABS_API_KEY") == "from-file"


def test_api_key_absent_is_none():
    assert config.load().api_key("elevenlabs", "ELEVENLABS_API_KEY") is None


def test_engine_options_are_exposed():
    write_config('[engines.piper]\nmodel = "en_GB-alba-medium"\n')
    assert config.load().options_for("piper") == {"model": "en_GB-alba-medium"}


def test_ensure_file_creates_a_private_template():
    path = config.ensure_file()
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "[defaults]" in path.read_text()


def test_ensure_file_does_not_clobber():
    write_config('[defaults]\naccent = "za"\n')
    assert 'accent = "za"' in config.ensure_file().read_text()
