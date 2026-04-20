"""User-tunable settings loaded from ``config.toml``."""

import dataclasses
import pathlib
import tomllib
import typing

from use_agent import config

DEFAULT_MODEL: str = 'claude-haiku-4-5-20251001'
DEFAULT_MAX_RESULTS: int = 25


DEFAULT_VOICE_GUIDELINES: tuple[str, ...] = (
    '1-2 sentences maximum, never more',
    'No apology for declining',
    "No explanation beyond what's necessary",
    'Blunt but not hostile',
    'Always ends with a remove request (except `specific_decline`)',
)

DEFAULT_REPLY_FOOTER: str = (
    '---\n'
    'This email was flagged as unsolicited sales outreach and this '
    "reply was sent on {{ user_name }}'s behalf by an automated "
    'assistant.'
)

DEFAULT_EXAMPLES_HARD_REMOVE: tuple[str, ...] = (
    "I'm not interested, please remove.",
    'No thanks, please remove me from your list.',
    'Not interested. Please remove me from your list.',
)

DEFAULT_EXAMPLES_HARD_REMOVE_WITH_CORRECTION: tuple[str, ...] = (
    'I think your list sold you the wrong contact at {{ organization }}.',
    "We're good. Please remove me from your list.",
    'Sorry, why are you sending this to me?',
)

DEFAULT_EXAMPLES_SPECIFIC_DECLINE: tuple[str, ...] = (
    "I really don't see a need to jump on a call right now. We're "
    'happy with our <Vendor Name> use, such a call would primarily '
    'benefit you, not us.',
    "It's just not a fit for us right now. I'll keep your info on "
    'file and reach out should that change.',
)


@dataclasses.dataclass(slots=True, frozen=True)
class Settings:
    """Runtime settings sourced from ``config.toml``."""

    user_name: str
    organization: str
    safelist_domains: tuple[str, ...]
    vendor_names: tuple[str, ...]
    voice_guidelines: tuple[str, ...]
    reply_footer: str
    examples_hard_remove: tuple[str, ...]
    examples_hard_remove_with_correction: tuple[str, ...]
    examples_specific_decline: tuple[str, ...]
    model: str
    search_query: str
    max_results: int

    @classmethod
    def load(cls, path: pathlib.Path | None = None) -> 'Settings':  # noqa: UP037
        """Load settings from a TOML file.

        The file is required; raises :class:`FileNotFoundError` if
        missing. Use the shipped ``config.example.toml`` as a
        starting point.
        """
        resolved = path or config.config_file_path()
        if not resolved.exists():
            raise FileNotFoundError(
                f'use-agent config not found at {resolved}. Copy '
                'config.example.toml from the project to that path '
                'and edit it, or set USE_AGENT_CONFIG.'
            )
        data = tomllib.loads(resolved.read_text(encoding='utf-8'))
        user = _section(data, 'user')
        safelist = _section(data, 'safelist')
        vendors = _section(data, 'vendors')
        voice = _section(data, 'voice')
        agent = _section(data, 'agent')
        search = _section(data, 'search')
        domains = tuple(
            str(d).strip().lower() for d in safelist.get('domains', ())
        )
        guidelines = (
            tuple(str(g) for g in voice.get('guidelines', ()))
            or DEFAULT_VOICE_GUIDELINES
        )
        footer_raw = voice.get('footer')
        footer = (
            str(footer_raw) if footer_raw is not None else DEFAULT_REPLY_FOOTER
        )
        examples = _section_opt(voice, 'examples')
        ex_hard = _example_list(
            examples, 'hard_remove', DEFAULT_EXAMPLES_HARD_REMOVE
        )
        ex_correct = _example_list(
            examples,
            'hard_remove_with_correction',
            DEFAULT_EXAMPLES_HARD_REMOVE_WITH_CORRECTION,
        )
        ex_specific = _example_list(
            examples,
            'specific_decline',
            DEFAULT_EXAMPLES_SPECIFIC_DECLINE,
        )
        query = search.get('query') or _build_query(domains)
        return cls(
            user_name=str(user.get('name', 'the user')),
            organization=str(user.get('organization', 'the organization')),
            safelist_domains=domains,
            vendor_names=tuple(str(v) for v in vendors.get('names', ())),
            voice_guidelines=guidelines,
            reply_footer=footer,
            examples_hard_remove=ex_hard,
            examples_hard_remove_with_correction=ex_correct,
            examples_specific_decline=ex_specific,
            model=str(agent.get('model', DEFAULT_MODEL)),
            search_query=str(query),
            max_results=int(search.get('max_results', DEFAULT_MAX_RESULTS)),
        )


def _section(data: dict[str, typing.Any], name: str) -> dict[str, typing.Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f'[{name}] must be a TOML table')
    return value


def _section_opt(
    data: dict[str, typing.Any], name: str
) -> dict[str, typing.Any]:
    """Like ``_section``, but absent key yields empty dict."""
    return _section(data, name) if name in data else {}


def _example_list(
    examples: dict[str, typing.Any],
    key: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    raw = examples.get(key)
    if raw is None:
        return default
    if not isinstance(raw, list):
        raise ValueError(f'[voice.examples] {key} must be a list of strings')
    return tuple(str(item) for item in raw)


def _build_query(domains: tuple[str, ...]) -> str:
    parts = ['in:inbox', 'is:unread']
    parts.extend(f'-from:{d}' for d in domains)
    return ' '.join(parts)
