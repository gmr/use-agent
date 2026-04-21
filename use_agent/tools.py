"""Tool definitions exposed to the Claude Agent SDK.

Each tool is a closure over a :class:`GmailClient`, then packaged
into an in-process MCP server via ``create_sdk_mcp_server``.
"""

import json
import logging
import typing

import claude_agent_sdk

from use_agent import cache as cache_mod
from use_agent import gmail

LOGGER = logging.getLogger(__name__)

MCP_SERVER_NAME = 'gmail'


def _text_result(obj: object) -> dict[str, typing.Any]:
    return {
        'content': [{'type': 'text', 'text': json.dumps(obj, default=str)}]
    }


def build_mcp_server(
    client: gmail.GmailClient,
    seen: cache_mod.Cache,
) -> typing.Any:
    """Return an SDK MCP server exposing Gmail operations.

    ``seen`` is the persisted cache of message_ids the agent has
    already investigated. The ``search`` tool filters it out of
    its results, and ``get_message`` adds to it, so a single message
    is only ever investigated once while it lives in the inbox.

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
        'Fetch a Gmail message including headers, body, and '
        'whether the thread already has a sent reply.',
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

    tools = [
        gmail_search,
        gmail_get_message,
        gmail_reply,
        gmail_archive_and_mark_read,
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
)
