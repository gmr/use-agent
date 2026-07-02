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
from use_agent import (
    storage as storage_mod,
)

LOGGER = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """\
You are use-agent, a triage assistant for {{ user_name }}'s Gmail
inbox. Your job is to find unsolicited sales email ("cold sales"),
reply to it on {{ user_name }}'s behalf in their voice, and then
archive the thread. You also identify unsolicited bulk marketing
(newsletters, product promos) and unsubscribe + delete them.

You have the following tools (prefixed with ``mcp__gmail__``):
- ``search``: run a Gmail search query, returns message ids
- ``get_message``: fetch full message (headers, body, thread state)
- ``reply``: send a threaded reply to a message
- ``archive_and_mark_read``: archive the thread and mark it read
- ``unsubscribe_and_trash``: honor the message's
  ``List-Unsubscribe`` header (RFC 8058 one-click HTTPS POST when
  available; else HTTPS GET; else a mailto unsubscribe) then Trash
  the thread
- ``trash``: move the thread to Trash without touching any
  unsubscribe endpoint
- ``record_action``: persist a message you acted on to the action
  history database (call once per acted-upon message)

## Workflow

1. Call ``search`` with the query provided in the user message.
2. For each returned message, call ``get_message``.
3. Skip any message where ``thread_replied`` is true or where the
   sender domain is in the safelist below. Skip BULK_MARKETING
   candidates that match the newsletter keep-list below (community
   lists or senders the user opted into).
4. Classify per the rules below. Then:
   - `COLD_SALES`: generate a reply body from the reply rules,
     call ``reply``, then ``archive_and_mark_read`` on success.
   - `BULK_MARKETING` with ``response_mode=unsubscribe_and_delete``:
     call ``unsubscribe_and_trash``, passing ``thread_id`` plus the
     ``list_unsubscribe`` and ``list_unsubscribe_post`` header
     values from ``get_message``. Do not send a reply.
   - `BULK_MARKETING` with ``response_mode=delete``: call
     ``trash``. Do not send a reply and do not click any in-body
     unsubscribe link — clicking validates the address to the
     sender.
   - `NOT_COLD_SALES`: take no action.
5. After each message you actually acted on (a reply, archive,
   unsubscribe, or trash completed successfully), call
   ``record_action`` once with ``message_id``, ``sender``,
   ``subject``, ``sent_at`` (the ``date`` from ``get_message``),
   ``classification``, ``category``, ``response_mode``,
   ``action_taken``, and ``score``. Do NOT call it for skipped
   messages, and do NOT call it when ``dry_run`` is true.
6. If ``dry_run`` is true, pass ``dry_run=true`` through to
   ``unsubscribe_and_trash`` / ``trash``, and skip ``reply`` +
   ``archive_and_mark_read`` entirely. Still report what you would
   have done.
7. When finished, emit a single fenced JSON block (no other JSON
   in your output) with a top-level ``results`` array. One entry
   per examined message; each entry has these keys:

   - ``message_id``: the Gmail ``message_id`` from ``get_message``
   - ``sender``: the original ``From`` header, including name
   - ``subject``: the original subject
   - ``date``: the original ``Date`` header value from
     ``get_message`` (copy it verbatim)
   - ``classification``: ``COLD_SALES``, ``BULK_MARKETING``, or
     ``NOT_COLD_SALES``
   - ``category``: a 1-5 word label summarizing what the message is
     about (e.g. ``Recruiter``, ``AI Solution``, ``Staff
     Augmentation``, ``SEO Services``, ``Newsletter``). Title Case.
   - ``score``: integer
   - ``response_mode``: ``hard_remove``,
     ``hard_remove_with_correction``, ``specific_decline``,
     ``unsubscribe_and_delete``, ``delete``, or ``none``
   - ``action_taken``: one of ``Reply sent & archived``,
     ``Dry-run: would reply & archive``,
     ``Reply sent & trashed``, ``Dry-run: would reply & trash``,
     ``Unsubscribed & trashed (<method>)``,
     ``Dry-run: would unsubscribe & trash (<method>)``,
     ``Trashed``, ``Dry-run: would trash``,
     ``Skipped (not cold sales)``, ``Skipped (kept: newsletter)``,
     ``Skipped (already replied)``,
     or ``Error: <detail>``

   Use a ```` ```json ```` fence. The block is parsed by the host
   program, so it must be valid JSON.

Never invent or fabricate email content. Never send anything other
than the reply text produced by the reply rules, or the
unsubscribe payloads produced by ``unsubscribe_and_trash``. If a
tool fails, record the error in the corresponding ``action_taken``
field and continue with the next message.

## Safelist domains

{% if safelist_domains %}
Treat senders from any of these domains as internal. Never classify
them as cold sales or bulk marketing, and never reply or
unsubscribe:

{% for d in safelist_domains %}
- {{ d }}
{% endfor %}
{% else %}
(none configured)
{% endif %}

## Newsletter keep-list

{{ newsletter_keep_block }}

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
        'newsletter_keep_domains': list(settings.newsletter_keep_domains),
        'newsletter_keep_list_ids': list(settings.newsletter_keep_list_ids),
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


def _render_newsletter_keep_block(
    keep_domains: tuple[str, ...],
    keep_list_ids: tuple[str, ...],
) -> str:
    """Pre-render the newsletter keep-list as Markdown.

    Rendered in Python (not Jinja) so blank lines survive the
    pre-commit Markdown formatter, which collapses them inside
    ``{% if %}`` blocks.
    """
    if not keep_domains and not keep_list_ids:
        return (
            'No newsletter keep-list configured. Treat every message '
            'matching BULK_MARKETING signals as bulk marketing.'
        )
    lines: list[str] = [
        'Do NOT classify a message as BULK_MARKETING if it matches '
        'either of the following. These are senders the user '
        'affirmatively opted into.',
        '',
    ]
    if keep_domains:
        lines.append('Kept sender domains:')
        lines.append('')
        lines.extend(f'- `{d}`' for d in keep_domains)
        lines.append('')
    if keep_list_ids:
        lines.append('Kept `List-Id` values:')
        lines.append('')
        lines.extend(f'- `{i}`' for i in keep_list_ids)
        lines.append('')
    lines.append(
        'When one of these matches, set `classification` to '
        '`NOT_COLD_SALES`, `response_mode` to `none`, and note '
        '"kept: newsletter match" in `notes`.'
    )
    return '\n'.join(lines)


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
    newsletter_keep_block = _render_newsletter_keep_block(
        settings.newsletter_keep_domains,
        settings.newsletter_keep_list_ids,
    )
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
        newsletter_keep_block=newsletter_keep_block,
        classifier=classifier.strip(),
        reply=reply.strip(),
    )


def _user_prompt(
    *,
    query: str,
    max_results: int,
    dry_run: bool,
    delete_only: bool,
    delete: bool,
) -> str:
    prompt = (
        'Process the Gmail inbox now.\n\n'
        f'search query: {query}\n'
        f'max_results: {max_results}\n'
        f'dry_run: {str(dry_run).lower()}\n'
    )
    if delete_only:
        prompt += (
            '\nOverride: when ``classification`` is ``COLD_SALES`` or '
            '``BULK_MARKETING``, do NOT call ``reply`` and do NOT call '
            '``unsubscribe_and_trash``. Call ``trash`` instead (passing '
            '``dry_run`` through). Set ``response_mode`` to ``delete`` '
            'and ``action_taken`` to ``Trashed`` (or '
            '``Dry-run: would trash`` when ``dry_run`` is true).\n'
        )
    elif delete:
        prompt += (
            '\nOverride: after replying to a ``COLD_SALES`` message, '
            'call ``trash`` instead of ``archive_and_mark_read`` '
            '(passing ``dry_run`` through). Set ``action_taken`` to '
            '``Reply sent & trashed`` (or ``Dry-run: would reply & '
            'trash`` when ``dry_run`` is true).\n'
        )
    return prompt


async def run(
    *,
    settings: settings_mod.Settings,
    reporter: reporter_mod.Reporter,
    query: str | None = None,
    max_results: int | None = None,
    lookback: str | None = None,
    dry_run: bool = False,
    delete_only: bool = False,
    delete: bool = False,
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
    # No history is written on a dry run; the store stays None and the
    # record_action tool no-ops.
    store = (
        None
        if dry_run
        else storage_mod.Store(
            config.db_path(),
            query_target=storage_mod.query_target(effective_query),
        )
    )
    server = tools.build_mcp_server(client, seen, store)
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
        delete_only=delete_only,
        delete=delete,
    )
    LOGGER.debug(
        'starting agent run: query=%r max=%d dry_run=%s delete_only=%s '
        'delete=%s model=%s',
        effective_query,
        effective_max,
        dry_run,
        delete_only,
        delete,
        settings.model,
    )
    async for message in claude_agent_sdk.query(
        prompt=prompt, options=options
    ):
        _forward(message, reporter)
    seen.save()
    rc = reporter.finish()
    if store is not None:
        _reconcile_history(store, reporter.summary)
        store.close()
    return rc


def _reconcile_history(
    store: storage_mod.Store,
    summary: list[dict[str, object]] | None,
) -> None:
    """Insert acted-upon summary rows the record_action tool missed.

    The tool is the primary capture path, but the model may forget to
    call it. Every acted-upon row in the final JSON summary that isn't
    already recorded this run is inserted, tagged ``source='summary'``.
    """
    if not summary:
        return
    for row in summary:
        action_taken = str(row.get('action_taken', ''))
        if not storage_mod.is_action(action_taken):
            continue
        message_id = str(row.get('message_id', '') or '')
        sender = str(row.get('sender', ''))
        subject = str(row.get('subject', ''))
        if store.has(message_id=message_id, sender=sender, subject=subject):
            continue
        store.record(
            sender=sender,
            subject=subject,
            sent_at=str(row.get('date', '') or ''),
            classification=str(row.get('classification', '')),
            category=str(row.get('category', '') or ''),
            response_mode=str(row.get('response_mode', '')),
            action_taken=action_taken,
            score=row.get('score'),
            message_id=message_id,
            source='summary',
        )


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
