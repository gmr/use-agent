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
