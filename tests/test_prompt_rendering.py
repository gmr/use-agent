"""Jinja rendering of the system prompt and per-mode example blocks."""

import pathlib
import textwrap

from use_agent import agent, settings


def _load(tmp_path: pathlib.Path, body: str) -> settings.Settings:
    path = tmp_path / 'c.toml'
    path.write_text(textwrap.dedent(body).lstrip(), encoding='utf-8')
    return settings.Settings.load(path)


def test_prompt_interpolates_user_and_org(tmp_path: pathlib.Path) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "Dana"
        organization = "Acme"
        """,
    )
    prompt = agent._render_system_prompt(s)
    assert 'Dana' in prompt
    assert 'Acme' in prompt


def test_safelist_and_vendors_land_in_prompt(
    tmp_path: pathlib.Path,
) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "x"
        organization = "y"

        [safelist]
        domains = ["internal.test", "staff.test"]

        [vendors]
        names = ["VendorOne", "VendorTwo"]
        """,
    )
    prompt = agent._render_system_prompt(s)
    assert 'internal.test' in prompt
    assert 'staff.test' in prompt
    assert 'VendorOne' in prompt
    assert 'VendorTwo' in prompt


def test_example_entries_are_themselves_rendered(
    tmp_path: pathlib.Path,
) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "Eva"
        organization = "Globex"

        [voice.examples]
        hard_remove_with_correction = [
            "Wrong {{ organization }} contact.",
        ]
        """,
    )
    prompt = agent._render_system_prompt(s)
    # Jinja inside an example entry must be evaluated, not passed through.
    assert 'Wrong Globex contact.' in prompt
    assert '{{ organization }}' not in prompt


def test_footer_jinja_is_rendered_once(tmp_path: pathlib.Path) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "Finn"
        organization = "Foo"

        [voice]
        footer = "-- on {{ user_name }}'s behalf"
        """,
    )
    prompt = agent._render_system_prompt(s)
    assert "on Finn's behalf" in prompt
    assert '{{ user_name }}' not in prompt


def test_empty_footer_suppresses_footer_block(
    tmp_path: pathlib.Path,
) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "x"
        organization = "y"

        [voice]
        footer = ""
        """,
    )
    prompt = agent._render_system_prompt(s)
    assert 'No footer — send the reply body verbatim' in prompt


def test_voice_guidelines_render_as_bullets(
    tmp_path: pathlib.Path,
) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "x"
        organization = "y"

        [voice]
        guidelines = ["first", "second"]
        """,
    )
    prompt = agent._render_system_prompt(s)
    assert '- first' in prompt
    assert '- second' in prompt


def test_newsletter_keep_lists_appear_in_prompt(
    tmp_path: pathlib.Path,
) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "x"
        organization = "y"

        [newsletters]
        keep_domains = ["github.com"]
        keep_list_ids = ["pgsql-general.lists.postgresql.org"]
        """,
    )
    prompt = agent._render_system_prompt(s)
    assert 'github.com' in prompt
    assert 'pgsql-general.lists.postgresql.org' in prompt
    assert 'affirmatively opted into' in prompt


def test_empty_newsletter_keep_list_has_default_copy(
    tmp_path: pathlib.Path,
) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "x"
        organization = "y"
        """,
    )
    prompt = agent._render_system_prompt(s)
    assert 'No newsletter keep-list configured' in prompt


def _prompt(**kwargs: object) -> str:
    defaults = {
        'query': 'in:inbox',
        'max_results': 25,
        'dry_run': False,
        'delete_only': False,
        'delete': False,
    }
    defaults.update(kwargs)
    return agent._user_prompt(**defaults)


def test_user_prompt_has_no_override_by_default() -> None:
    assert 'Override' not in _prompt()


def test_user_prompt_delete_only_override() -> None:
    prompt = _prompt(delete_only=True)
    assert 'do NOT call ``reply``' in prompt
    assert 'Call ``trash`` instead' in prompt


def test_user_prompt_delete_replies_then_trashes() -> None:
    prompt = _prompt(delete=True)
    assert 'after replying' in prompt
    assert 'instead of ``archive_and_mark_read``' in prompt
    assert 'Reply sent & trashed' in prompt


def test_user_prompt_delete_only_takes_precedence() -> None:
    # delete_only wins if both are somehow set (CLI forbids it).
    prompt = _prompt(delete_only=True, delete=True)
    assert 'do NOT call ``reply``' in prompt
    assert 'after replying' not in prompt


def test_bulk_marketing_response_modes_documented(
    tmp_path: pathlib.Path,
) -> None:
    s = _load(
        tmp_path,
        """
        [user]
        name = "x"
        organization = "y"
        """,
    )
    prompt = agent._render_system_prompt(s)
    # The full set of response modes must appear so the agent has
    # the vocabulary needed to emit valid JSON rows.
    assert 'unsubscribe_and_delete' in prompt
    assert 'BULK_MARKETING' in prompt
    assert 'unsubscribe_and_trash' in prompt
    assert 'trash' in prompt
