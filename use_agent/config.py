"""Paths and static defaults for use-agent."""

import os
import pathlib

# Single scope. gmail.modify covers read, label changes, and send.
GMAIL_SCOPES: tuple[str, ...] = (
    'https://www.googleapis.com/auth/gmail.modify',
)

PROMPTS_DIR: pathlib.Path = pathlib.Path(__file__).parent / 'prompts'
CLASSIFIER_PROMPT: pathlib.Path = PROMPTS_DIR / 'classifier.md'
REPLY_PROMPT: pathlib.Path = PROMPTS_DIR / 'reply.md'


def _config_home() -> pathlib.Path:
    raw = os.environ.get('XDG_CONFIG_HOME')
    if raw:
        return pathlib.Path(raw) / 'use-agent'
    return pathlib.Path.home() / '.config' / 'use-agent'


def credentials_path() -> pathlib.Path:
    """Path to the Google OAuth client secret JSON."""
    override = os.environ.get('USE_AGENT_CREDENTIALS')
    if override:
        return pathlib.Path(override)
    return _config_home() / 'credentials.json'


def token_path() -> pathlib.Path:
    """Path to the stored OAuth refresh token."""
    override = os.environ.get('USE_AGENT_TOKEN')
    if override:
        return pathlib.Path(override)
    return _config_home() / 'token.json'


def config_file_path() -> pathlib.Path:
    """Path to the TOML configuration file."""
    override = os.environ.get('USE_AGENT_CONFIG')
    if override:
        return pathlib.Path(override)
    return _config_home() / 'config.toml'


def cache_path() -> pathlib.Path:
    """Path to the JSON-backed seen-message cache."""
    override = os.environ.get('USE_AGENT_CACHE')
    if override:
        return pathlib.Path(override)
    return _config_home() / 'cache.json'


def db_path() -> pathlib.Path:
    """Path to the SQLite action-history database."""
    override = os.environ.get('USE_AGENT_DB')
    if override:
        return pathlib.Path(override)
    return _config_home() / 'actions.db'
