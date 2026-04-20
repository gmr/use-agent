"""Path resolution and env-var overrides in ``use_agent.config``."""

import pathlib

import pytest

from use_agent import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        'USE_AGENT_CONFIG',
        'USE_AGENT_CREDENTIALS',
        'USE_AGENT_TOKEN',
        'XDG_CONFIG_HOME',
    ):
        monkeypatch.delenv(var, raising=False)


def test_default_paths_use_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv('HOME', str(tmp_path))
    assert config.config_file_path() == (
        tmp_path / '.config' / 'use-agent' / 'config.toml'
    )
    assert config.credentials_path() == (
        tmp_path / '.config' / 'use-agent' / 'credentials.json'
    )
    assert config.token_path() == (
        tmp_path / '.config' / 'use-agent' / 'token.json'
    )


def test_xdg_config_home_wins_over_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    xdg = tmp_path / 'xdg'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(xdg))
    assert config.config_file_path() == xdg / 'use-agent' / 'config.toml'
    assert config.token_path() == xdg / 'use-agent' / 'token.json'


def test_env_overrides_win_over_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    cfg = tmp_path / 'custom.toml'
    creds = tmp_path / 'creds.json'
    token = tmp_path / 'token.json'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setenv('USE_AGENT_CONFIG', str(cfg))
    monkeypatch.setenv('USE_AGENT_CREDENTIALS', str(creds))
    monkeypatch.setenv('USE_AGENT_TOKEN', str(token))
    assert config.config_file_path() == cfg
    assert config.credentials_path() == creds
    assert config.token_path() == token


def test_prompt_paths_resolve_to_shipped_files() -> None:
    assert config.CLASSIFIER_PROMPT.exists()
    assert config.REPLY_PROMPT.exists()
    assert config.PROMPTS_DIR.is_dir()
