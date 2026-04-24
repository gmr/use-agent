"""Loading ``config.toml`` into a ``Settings`` instance."""

import pathlib
import textwrap

import pytest

from use_agent import settings


def _write(path: pathlib.Path, body: str) -> pathlib.Path:
    path.write_text(textwrap.dedent(body).lstrip(), encoding='utf-8')
    return path


def test_missing_file_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        settings.Settings.load(tmp_path / 'nope.toml')


def test_minimal_config_fills_defaults(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "Alice"
        organization = "AcmeCo"
        """,
    )
    s = settings.Settings.load(cfg)

    assert s.user_name == 'Alice'
    assert s.organization == 'AcmeCo'
    assert s.safelist_domains == ()
    assert s.vendor_names == ()
    assert s.voice_guidelines == settings.DEFAULT_VOICE_GUIDELINES
    assert s.reply_footer == settings.DEFAULT_REPLY_FOOTER
    assert s.examples_hard_remove == settings.DEFAULT_EXAMPLES_HARD_REMOVE
    assert s.examples_hard_remove_with_correction == (
        settings.DEFAULT_EXAMPLES_HARD_REMOVE_WITH_CORRECTION
    )
    assert s.examples_specific_decline == (
        settings.DEFAULT_EXAMPLES_SPECIFIC_DECLINE
    )
    assert s.newsletter_keep_domains == ()
    assert s.newsletter_keep_list_ids == ()
    assert s.model == settings.DEFAULT_MODEL
    assert s.max_results == settings.DEFAULT_MAX_RESULTS
    # No safelist → query has just the baseline operators.
    assert s.search_query == 'in:inbox is:unread'


def test_full_config_all_fields(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "Bob"
        organization = "Widgets Inc"

        [safelist]
        domains = ["Widgets.com", "widgets.net"]

        [vendors]
        names = ["AWS", "GitHub"]

        [voice]
        guidelines = ["be curt", "no fluff"]
        footer = "-- sent by use-agent on {{ user_name }}'s behalf"

        [voice.examples]
        hard_remove = ["no thanks"]
        hard_remove_with_correction = ["wrong {{ organization }}."]
        specific_decline = ["call not needed"]

        [agent]
        model = "claude-test-1"

        [search]
        max_results = 7
        query = "in:inbox newer_than:1d"
        """,
    )
    s = settings.Settings.load(cfg)

    assert s.user_name == 'Bob'
    assert s.organization == 'Widgets Inc'
    # Safelist domains are lowercased + stripped.
    assert s.safelist_domains == ('widgets.com', 'widgets.net')
    assert s.vendor_names == ('AWS', 'GitHub')
    assert s.voice_guidelines == ('be curt', 'no fluff')
    assert s.reply_footer.startswith('-- sent by use-agent')
    assert s.examples_hard_remove == ('no thanks',)
    assert s.examples_hard_remove_with_correction == (
        'wrong {{ organization }}.',
    )
    assert s.examples_specific_decline == ('call not needed',)
    assert s.model == 'claude-test-1'
    assert s.max_results == 7
    # Explicit query overrides the safelist-derived one.
    assert s.search_query == 'in:inbox newer_than:1d'


def test_query_built_from_safelist_when_absent(
    tmp_path: pathlib.Path,
) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"

        [safelist]
        domains = ["one.com", "two.com"]
        """,
    )
    s = settings.Settings.load(cfg)
    assert s.search_query == ('in:inbox is:unread -from:one.com -from:two.com')


def test_footer_empty_string_is_preserved(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"

        [voice]
        footer = ""
        """,
    )
    s = settings.Settings.load(cfg)
    # Empty string must NOT fall back to the default.
    assert s.reply_footer == ''


def test_examples_partial_override_falls_back_per_key(
    tmp_path: pathlib.Path,
) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"

        [voice.examples]
        hard_remove = ["only this"]
        """,
    )
    s = settings.Settings.load(cfg)
    assert s.examples_hard_remove == ('only this',)
    # The two untouched keys retain their module-level defaults.
    assert s.examples_hard_remove_with_correction == (
        settings.DEFAULT_EXAMPLES_HARD_REMOVE_WITH_CORRECTION
    )
    assert s.examples_specific_decline == (
        settings.DEFAULT_EXAMPLES_SPECIFIC_DECLINE
    )


def test_bad_section_type_raises(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        user = "not-a-table"
        """,
    )
    with pytest.raises(ValueError, match=r'\[user\] must be a TOML table'):
        settings.Settings.load(cfg)


def test_bad_example_list_raises(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"

        [voice.examples]
        hard_remove = "not a list"
        """,
    )
    with pytest.raises(ValueError, match=r'hard_remove must be a list'):
        settings.Settings.load(cfg)


def test_build_query_preserves_domain_order() -> None:
    assert settings._build_query(('a.com', 'b.com', 'c.com')) == (
        'in:inbox is:unread -from:a.com -from:b.com -from:c.com'
    )


def test_build_query_no_domains() -> None:
    assert settings._build_query(()) == 'in:inbox is:unread'


def test_lookback_absent_is_none(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"
        """,
    )
    assert settings.Settings.load(cfg).lookback is None


def test_lookback_parsed_and_normalized(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"

        [search]
        lookback = "7D"
        """,
    )
    s = settings.Settings.load(cfg)
    assert s.lookback == '7d'
    # search_query is stored without lookback; agent.run composes it.
    assert s.search_query == 'in:inbox is:unread'


def test_newsletter_keep_lists_loaded(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"

        [newsletters]
        keep_domains = ["GitHub.com", "lwn.net"]
        keep_list_ids = ["pgsql-general.lists.postgresql.org"]
        """,
    )
    s = settings.Settings.load(cfg)
    # keep_domains are lowercased to match how senders are compared.
    assert s.newsletter_keep_domains == ('github.com', 'lwn.net')
    # List-Id values are case-sensitive in practice; left as-is.
    assert s.newsletter_keep_list_ids == (
        'pgsql-general.lists.postgresql.org',
    )


def test_lookback_invalid_raises(tmp_path: pathlib.Path) -> None:
    cfg = _write(
        tmp_path / 'c.toml',
        """
        [user]
        name = "x"
        organization = "y"

        [search]
        lookback = "2w"
        """,
    )
    with pytest.raises(ValueError, match='invalid lookback'):
        settings.Settings.load(cfg)
