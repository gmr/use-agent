"""Tool definitions exposed to the Claude Agent SDK.

Each tool is a closure over a :class:`GmailClient`, then packaged
into an in-process MCP server via ``create_sdk_mcp_server``.
"""

import json
import logging
import typing
import urllib.error
import urllib.parse
import urllib.request

import claude_agent_sdk

from use_agent import cache as cache_mod
from use_agent import gmail
from use_agent import storage as storage_mod

LOGGER = logging.getLogger(__name__)

MCP_SERVER_NAME = 'gmail'

# Don't let a stalled unsubscribe endpoint hang the whole run.
_UNSUB_HTTP_TIMEOUT: float = 10.0
_UNSUB_USER_AGENT: str = 'use-agent-unsubscribe/1.0'

_METHOD_ONE_CLICK = 'one_click_post'
_METHOD_HTTP_GET = 'http_get'
_METHOD_MAILTO = 'mailto'
_METHOD_NONE = 'none'


def _text_result(obj: object) -> dict[str, typing.Any]:
    return {
        'content': [{'type': 'text', 'text': json.dumps(obj, default=str)}]
    }


def _pick_unsubscribe_action(
    list_unsubscribe: str, list_unsubscribe_post: str
) -> tuple[str, object]:
    """Return ``(method, target)`` for the best available endpoint."""
    targets = gmail.unsubscribe_targets(
        list_unsubscribe, list_unsubscribe_post
    )
    http_urls = typing.cast('list[str]', targets['http_urls'])
    mailtos = typing.cast('list[dict[str, str]]', targets['mailtos'])
    https = [u for u in http_urls if u.lower().startswith('https://')]
    if targets['one_click'] and https:
        return _METHOD_ONE_CLICK, https[0]
    if https:
        return _METHOD_HTTP_GET, https[0]
    if mailtos:
        return _METHOD_MAILTO, mailtos[0]
    return _METHOD_NONE, None


def _do_unsubscribe_and_trash(
    client: gmail.GmailClient,
    *,
    thread_id: str,
    list_unsubscribe: str,
    list_unsubscribe_post: str,
    dry_run: bool,
) -> dict[str, typing.Any]:
    method, target = _pick_unsubscribe_action(
        list_unsubscribe, list_unsubscribe_post
    )
    if dry_run:
        return {
            'dry_run': True,
            'method': method,
            'detail': _describe_planned(method, target),
            'trashed': False,
        }
    ok, detail = _perform(client, method, target)
    client.trash_thread(thread_id)
    return {
        'dry_run': False,
        'method': method,
        'ok': ok,
        'detail': detail,
        'trashed': True,
    }


def _describe_planned(method: str, target: object) -> str:
    if method == _METHOD_ONE_CLICK:
        return f'would POST {target}'
    if method == _METHOD_HTTP_GET:
        return f'would GET {target}'
    if method == _METHOD_MAILTO:
        return f'would mail {typing.cast("dict[str, str]", target)["address"]}'
    return 'no List-Unsubscribe header'


def _perform(
    client: gmail.GmailClient, method: str, target: object
) -> tuple[bool, str]:
    if method == _METHOD_ONE_CLICK:
        return _http_unsubscribe(typing.cast('str', target), one_click=True)
    if method == _METHOD_HTTP_GET:
        return _http_unsubscribe(typing.cast('str', target), one_click=False)
    if method == _METHOD_MAILTO:
        entry = typing.cast('dict[str, str]', target)
        try:
            sent_id = client.send_unsubscribe_mail(
                to=entry['address'],
                subject=entry['subject'],
                body=entry['body'],
            )
        except Exception as exc:  # noqa: BLE001 - report, don't fail run
            return False, f'{type(exc).__name__}: {exc}'
        return True, f'sent {sent_id}'
    return False, 'no List-Unsubscribe header'


def _http_unsubscribe(url: str, *, one_click: bool) -> tuple[bool, str]:
    """Hit an HTTPS unsubscribe endpoint.

    For RFC 8058 one-click we POST ``List-Unsubscribe=One-Click`` as a
    form body. Otherwise we issue a GET. 2xx/3xx is treated as
    success; anything else is surfaced so the agent can report it.
    """
    try:
        if one_click:
            data = urllib.parse.urlencode(
                {'List-Unsubscribe': 'One-Click'}
            ).encode('ascii')
            req = urllib.request.Request(  # noqa: S310 - scheme checked below
                url,
                data=data,
                method='POST',
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': _UNSUB_USER_AGENT,
                },
            )
        else:
            req = urllib.request.Request(  # noqa: S310 - scheme checked below
                url,
                method='GET',
                headers={'User-Agent': _UNSUB_USER_AGENT},
            )
        if req.type not in ('http', 'https'):
            return False, f'refused non-http(s) scheme: {req.type}'
        with urllib.request.urlopen(  # noqa: S310 - scheme validated above
            req, timeout=_UNSUB_HTTP_TIMEOUT
        ) as resp:
            status = getattr(resp, 'status', 0)
            return (200 <= status < 400), f'HTTP {status}'
    except urllib.error.HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f'{type(exc).__name__}: {exc}'


def _build_record_action_tool(store: storage_mod.Store | None) -> typing.Any:
    """Build the ``record_action`` tool bound to ``store``.

    Extracted from :func:`build_mcp_server` to keep that function
    under the complexity limit. A ``None`` store makes the tool a
    no-op (used on ``--dry-run``).
    """

    @claude_agent_sdk.tool(
        'record_action',
        (
            'Persist a message that was acted upon to the action '
            'history. Call this once after each successful reply, '
            'archive, unsubscribe, or trash — never for skipped '
            'messages and never on a dry run. Copy `sender`, '
            '`subject`, and `sent_at` (the message `date`) from the '
            'preceding `get_message`; pass the `classification`, '
            '`category` (a 1-5 word content label), `response_mode`, '
            '`action_taken`, and `score` you assigned.'
        ),
        {
            'message_id': str,
            'sender': str,
            'subject': str,
            'sent_at': str,
            'classification': str,
            'category': str,
            'response_mode': str,
            'action_taken': str,
            'score': int,
        },
    )
    async def gmail_record_action(
        args: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        if store is None:
            return _text_result({'recorded': False, 'reason': 'no store'})
        store.record(
            sender=args.get('sender', ''),
            subject=args.get('subject', ''),
            sent_at=args.get('sent_at', ''),
            classification=args.get('classification', ''),
            category=args.get('category', ''),
            response_mode=args.get('response_mode', ''),
            action_taken=args.get('action_taken', ''),
            score=args.get('score'),
            message_id=args.get('message_id', ''),
            source='tool',
        )
        return _text_result({'recorded': True})

    return gmail_record_action


def build_mcp_server(
    client: gmail.GmailClient,
    seen: cache_mod.Cache,
    store: storage_mod.Store | None = None,
) -> typing.Any:
    """Return an SDK MCP server exposing Gmail operations.

    ``seen`` is the persisted cache of message_ids the agent has
    already investigated. The ``search`` tool filters it out of
    its results, and ``get_message`` adds to it, so a single message
    is only ever investigated once while it lives in the inbox.

    ``store`` is the action-history database. When ``None`` (e.g. a
    ``--dry-run``), the ``record_action`` tool becomes a no-op.

    The server name is :data:`MCP_SERVER_NAME`; each tool is reachable
    from the agent as ``mcp__gmail__<tool>``.
    """

    @claude_agent_sdk.tool(
        'search',
        'Search the Gmail inbox and return message ids. Messages '
        'already processed in a prior run are filtered out '
        'automatically, so the result may be smaller than max_results.',
        {'query': str, 'max_results': int},
    )
    async def gmail_search(
        args: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        query = args['query']
        max_results = int(args.get('max_results') or 25)
        LOGGER.debug('gmail_search q=%r max=%d', query, max_results)
        results = client.search(query, max_results=max_results)
        filtered = [r for r in results if r['message_id'] not in seen]
        skipped = len(results) - len(filtered)
        if skipped:
            LOGGER.debug('gmail_search: skipped %d cached message(s)', skipped)
        return _text_result(filtered)

    @claude_agent_sdk.tool(
        'get_message',
        'Fetch a Gmail message including headers, body, whether the '
        'thread already has a sent reply (thread_replied), and '
        'whether the user has previously emailed this sender in any '
        'thread (prior_correspondence).',
        {'message_id': str},
    )
    async def gmail_get_message(
        args: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        msg = client.get_message(args['message_id'])
        seen.add(args['message_id'])
        return _text_result(msg.to_dict())

    @claude_agent_sdk.tool(
        'reply',
        'Send a threaded reply to a Gmail message. Gmail will '
        'render it as a reply in the original thread. The body is '
        'the exact text to send — do not add a signature.',
        {'message_id': str, 'body': str},
    )
    async def gmail_reply(
        args: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        sent_id = client.reply(
            message_id=args['message_id'],
            body=args['body'],
        )
        return _text_result({'sent_message_id': sent_id})

    @claude_agent_sdk.tool(
        'archive_and_mark_read',
        'Archive a Gmail thread (remove INBOX label) and mark the '
        'given message as read (remove UNREAD label).',
        {'message_id': str, 'thread_id': str},
    )
    async def gmail_archive_and_mark_read(
        args: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        client.archive_thread(args['thread_id'])
        client.mark_read(args['message_id'])
        return _text_result({'archived': True, 'marked_read': True})

    @claude_agent_sdk.tool(
        'unsubscribe_and_trash',
        (
            "Honor the message's List-Unsubscribe header, then move "
            'the thread to Trash. Pass the `list_unsubscribe` and '
            '`list_unsubscribe_post` header values returned from '
            '`get_message`. Prefers RFC 8058 one-click (HTTPS POST); '
            'falls back to HTTPS GET; falls back to sending a mailto '
            'unsubscribe. Set dry_run=true to log what would happen '
            'without touching the network or mailbox.'
        ),
        {
            'thread_id': str,
            'list_unsubscribe': str,
            'list_unsubscribe_post': str,
            'dry_run': bool,
        },
    )
    async def gmail_unsubscribe_and_trash(
        args: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        return _text_result(
            _do_unsubscribe_and_trash(
                client,
                thread_id=args['thread_id'],
                list_unsubscribe=args.get('list_unsubscribe', ''),
                list_unsubscribe_post=args.get('list_unsubscribe_post', ''),
                dry_run=bool(args.get('dry_run', False)),
            )
        )

    @claude_agent_sdk.tool(
        'trash',
        'Move a Gmail thread to Trash without attempting to '
        'unsubscribe. Use for bulk marketing that lacks a '
        'List-Unsubscribe header and only exposes an in-body '
        'unsubscribe link (clicking would validate the address). '
        'Set dry_run=true to log what would happen.',
        {'thread_id': str, 'dry_run': bool},
    )
    async def gmail_trash(
        args: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        thread_id = args['thread_id']
        dry_run = bool(args.get('dry_run', False))
        if dry_run:
            return _text_result({'dry_run': True, 'trashed': False})
        client.trash_thread(thread_id)
        return _text_result({'dry_run': False, 'trashed': True})

    tools = [
        gmail_search,
        gmail_get_message,
        gmail_reply,
        gmail_archive_and_mark_read,
        gmail_unsubscribe_and_trash,
        gmail_trash,
        _build_record_action_tool(store),
    ]
    return claude_agent_sdk.create_sdk_mcp_server(
        name=MCP_SERVER_NAME,
        version='1.0.0',
        tools=tools,
    )


ALLOWED_TOOLS: tuple[str, ...] = (
    f'mcp__{MCP_SERVER_NAME}__search',
    f'mcp__{MCP_SERVER_NAME}__get_message',
    f'mcp__{MCP_SERVER_NAME}__reply',
    f'mcp__{MCP_SERVER_NAME}__archive_and_mark_read',
    f'mcp__{MCP_SERVER_NAME}__unsubscribe_and_trash',
    f'mcp__{MCP_SERVER_NAME}__trash',
    f'mcp__{MCP_SERVER_NAME}__record_action',
)
