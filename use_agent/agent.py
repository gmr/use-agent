"""Orchestrates a Claude Agent run over the Gmail inbox."""

import logging

import claude_agent_sdk
import jinja2

from use_agent import (
    auth,
    config,
    gmail,
    tools,
)
from use_agent import (
    cache as cache_mod,
)
from use_agent import (
    reporter as reporter_mod,
)
from use_agent import (
    settings as settings_mod,
)

LOGGER = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """\
You are use-agent, a triage assistant for {{ user_name }}'s Gmail
inbox. Your job is to find unsolicited sales email ("cold sales"),
reply to it on {{ user_name }}'s behalf in their voice, and then
archive the thread.

You have the following tools (prefixed with ``mcp__gmail__``):
- ``search``: run a Gmail search query, returns message ids
- ``get_message``: fetch full message (headers, body, thread state)
- ``reply``: send a threaded reply to a message
- ``archive_and_mark_read``: archive the thread and mark it read

## Workflow

1. Call ``search`` with the query provided in the user message.
2. For each returned message, call ``get_message``.
3. Skip any message where ``thread_replied`` is true, where the
   sender domain is in the safelist below, or where the classifier
   result is not COLD_SALES.
4. For each COLD_SALES message, generate a reply body using the
   reply rules below, then call ``reply`` with that body. On
   success, call ``archive_and_mark_read``.
5. If ``dry_run`` is true, do everything except call ``reply`` and
   ``archive_and_mark_read``. Still report what you would have done.
6. When finished, emit a single fenced JSON block (no other JSON
   in your output) with a top-level ``results`` array. One entry
   per examined message; each entry has these keys:

   - ``sender``: the original ``From`` header, including name
   - ``subject``: the original subject
   - ``classification``: ``COLD_SALES`` or ``NOT_COLD_SALES``
   - ``score``: integer
   - ``response_mode``: ``hard_remove``,
     ``hard_remove_with_correction``, ``specific_decline``, or
     ``none``
   - ``action_taken``: one of ``Reply sent & archived``,
     ``Dry-run: would reply & archive``,
     ``Skipped (not cold sales)``, ``Skipped (already replied)``,
     or ``Error: <detail>``

   Use a ```` ```json ```` fence. The block is parsed by the host
   program, so it must be valid JSON.

Never invent or fabricate email content. Never send anything other
than the reply text produced by the reply rules. If a tool fails,
record the error in the corresponding ``action_taken`` field and
continue with the next message.

## Safelist domains

{% if safelist_domains %}
Treat senders from any of these domains as internal. Never classify
them as cold sales and never reply:

{% for d in safelist_domains %}
- {{ d }}
{% endfor %}
{% else %}
(none configured)
{% endif %}

## Classification rules

{{ classifier }}

## Reply rules

{{ reply }}
"""


def _jinja_env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(config.PROMPTS_DIR)),
        autoescape=False,  # noqa: S701 - prompts are fed to an LLM, not rendered as HTML
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _render_template(
    env: jinja2.Environment,
    name: str,
    settings: settings_mod.Settings,
) -> str:
    template = env.get_template(name)
    return template.render(_render_context(env, settings))


def _render_context(
    env: jinja2.Environment,
    settings: settings_mod.Settings,
) -> dict[str, object]:
    base = {
        'user_name': settings.user_name,
        'organization': settings.organization,
        'safelist_domains': list(settings.safelist_domains),
        'vendor_names': list(settings.vendor_names),
        'voice_guidelines': list(settings.voice_guidelines),
    }
    # The footer may itself be a Jinja string (e.g. "{{ user_name }}").
    rendered_footer = (
        env.from_string(settings.reply_footer).render(base)
        if settings.reply_footer
        else ''
    )
    voice_block = '\n'.join(f'- {g}' for g in settings.voice_guidelines)
    # Example bullets may themselves contain Jinja refs like
    # {{ organization }}; render each one against the base context
    # before joining so interpolation happens exactly once.
    hard_remove_examples = _render_examples(
        env, settings.examples_hard_remove, base
    )
    hard_remove_with_correction_examples = _render_examples(
        env, settings.examples_hard_remove_with_correction, base
    )
    specific_decline_examples = _render_examples(
        env, settings.examples_specific_decline, base
    )
    # Pre-rendered blocks are inserted verbatim so prompt markdown
    # stays free of Jinja control flow (formatters don't preserve
    # blank lines inside {% if %} blocks).
    if rendered_footer:
        footer_block = f'\n\n{rendered_footer}'
        footer_instruction = (
            'Every reply must end with the following footer, '
            'appended after the reply body on a new line, '
            'separated by a blank line:\n\n'
            f'```\n{rendered_footer}\n```'
        )
    else:
        footer_block = ''
        footer_instruction = (
            'No footer — send the reply body verbatim with no trailing text.'
        )
    return {
        **base,
        'reply_footer': rendered_footer,
        'voice_block': voice_block,
        'footer_block': footer_block,
        'footer_instruction': footer_instruction,
        'hard_remove_examples': hard_remove_examples,
        'hard_remove_with_correction_examples': (
            hard_remove_with_correction_examples
        ),
        'specific_decline_examples': specific_decline_examples,
    }


def _render_examples(
    env: jinja2.Environment,
    items: tuple[str, ...],
    context: dict[str, object],
) -> str:
    """Render each example as a Markdown bullet.

    An empty list collapses to the single line "(none configured)"
    so the rendered prompt always has something under the heading.
    """
    if not items:
        return '(none configured)'
    bullets: list[str] = []
    for item in items:
        rendered = env.from_string(item).render(context).strip()
        bullets.append(f'- "{rendered}"')
    return '\n'.join(bullets)


def _render_system_prompt(settings: settings_mod.Settings) -> str:
    env = _jinja_env()
    classifier = _render_template(env, config.CLASSIFIER_PROMPT.name, settings)
    reply = _render_template(env, config.REPLY_PROMPT.name, settings)
    system_template = jinja2.Environment(
        autoescape=False,  # noqa: S701 - prompts are fed to an LLM, not rendered as HTML
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    ).from_string(SYSTEM_PROMPT_TEMPLATE)
    return system_template.render(
        user_name=settings.user_name,
        safelist_domains=list(settings.safelist_domains),
        classifier=classifier.strip(),
        reply=reply.strip(),
    )


def _user_prompt(*, query: str, max_results: int, dry_run: bool) -> str:
    return (
        'Process the Gmail inbox now.\n\n'
        f'search query: {query}\n'
        f'max_results: {max_results}\n'
        f'dry_run: {str(dry_run).lower()}\n'
    )


async def run(
    *,
    settings: settings_mod.Settings,
    reporter: reporter_mod.Reporter,
    query: str | None = None,
    max_results: int | None = None,
    lookback: str | None = None,
    dry_run: bool = False,
) -> int:
    """Execute a single agent pass over the inbox.

    Returns a process exit code — 0 on a successful run with a
    parsed summary, non-zero if the agent produced no summary.
    """
    effective_query = query or settings.search_query
    effective_lookback = (
        settings_mod.validate_lookback(lookback)
        if lookback is not None
        else settings.lookback
    )
    if effective_lookback:
        effective_query = f'{effective_query} newer_than:{effective_lookback}'
    effective_max = max_results or settings.max_results
    creds = auth.load_credentials(
        credentials_file=config.credentials_path(),
        token_file=config.token_path(),
        scopes=config.GMAIL_SCOPES,
    )
    client = gmail.GmailClient(creds)
    seen = _load_and_prune_cache(client)
    server = tools.build_mcp_server(client, seen)
    options = claude_agent_sdk.ClaudeAgentOptions(
        system_prompt=_render_system_prompt(settings),
        mcp_servers={tools.MCP_SERVER_NAME: server},
        allowed_tools=list(tools.ALLOWED_TOOLS),
        permission_mode='acceptEdits',
        model=settings.model,
        # Isolate from the host's Claude Code setup: no user/project
        # settings, no on-disk settings file, no auto-loaded skills.
        # Only our Gmail MCP server and system prompt drive behavior.
        setting_sources=[],
        settings=None,
        skills=None,
    )
    prompt = _user_prompt(
        query=effective_query,
        max_results=effective_max,
        dry_run=dry_run,
    )
    LOGGER.debug(
        'starting agent run: query=%r max=%d dry_run=%s model=%s',
        effective_query,
        effective_max,
        dry_run,
        settings.model,
    )
    async for message in claude_agent_sdk.query(
        prompt=prompt, options=options
    ):
        _forward(message, reporter)
    seen.save()
    return reporter.finish()


def _forward(message: object, reporter: reporter_mod.Reporter) -> None:
    """Feed assistant text blocks to the reporter."""
    if isinstance(message, claude_agent_sdk.AssistantMessage):
        for block in message.content:
            if isinstance(block, claude_agent_sdk.TextBlock):
                reporter.on_text(block.text)


def _load_and_prune_cache(
    client: gmail.GmailClient,
) -> cache_mod.Cache:
    """Load the seen-message cache and drop entries no longer in inbox.

    A listing failure (network error, auth hiccup, pagination cap)
    degrades gracefully: the cache is left untouched rather than
    wiped, so a transient failure can't force re-investigation of
    every cached message.
    """
    seen = cache_mod.Cache.load(config.cache_path())
    try:
        inbox_ids = client.list_inbox_message_ids()
    except Exception:
        LOGGER.exception('failed to list inbox; skipping cache prune')
        inbox_ids = None
    if inbox_ids is not None:
        dropped = seen.retain(inbox_ids)
        if dropped:
            LOGGER.debug('pruned %d cache entries no longer in inbox', dropped)
    LOGGER.debug('seen-message cache: %d entries', len(seen))
    return seen
