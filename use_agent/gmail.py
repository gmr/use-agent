"""Gmail API client used by the agent's tools.

Returns plain, JSON-friendly data so that results flow cleanly back
into the Claude Agent SDK as tool output.
"""

import base64
import dataclasses
import email.message
import email.policy
import email.utils
import logging
import urllib.parse

import google.oauth2.credentials
import googleapiclient.discovery

LOGGER = logging.getLogger(__name__)

# Guard against huge inboxes: each page costs one API call. 20 pages
# of 500 covers 10,000 messages — well past any realistic use.
_INBOX_LIST_PAGE_CAP: int = 20
_INBOX_LIST_PAGE_SIZE: int = 500

# A sender is treated as an established inbound contact (not cold
# outreach) if they've been emailing since at least this long before
# the message under review. Cold sequences are compressed into days;
# a real relationship spans months.
_RELATIONSHIP_MIN_AGE_DAYS: int = 60
_SECONDS_PER_DAY: int = 86400


@dataclasses.dataclass(slots=True, frozen=True)
class Message:
    """Structured view of a Gmail message the agent reasons about."""

    message_id: str
    thread_id: str
    rfc822_message_id: str
    references: str
    from_header: str
    to_header: str
    subject: str
    date: str
    body: str
    snippet: str
    thread_replied: bool
    prior_correspondence: bool
    label_ids: tuple[str, ...]
    list_unsubscribe: str
    list_unsubscribe_post: str
    list_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            'message_id': self.message_id,
            'thread_id': self.thread_id,
            'rfc822_message_id': self.rfc822_message_id,
            'from': self.from_header,
            'to': self.to_header,
            'subject': self.subject,
            'date': self.date,
            'body': self.body,
            'snippet': self.snippet,
            'thread_replied': self.thread_replied,
            'prior_correspondence': self.prior_correspondence,
            'labels': list(self.label_ids),
            'list_unsubscribe': self.list_unsubscribe,
            'list_unsubscribe_post': self.list_unsubscribe_post,
            'list_id': self.list_id,
        }


def unsubscribe_targets(
    list_unsubscribe: str,
    list_unsubscribe_post: str,
) -> dict[str, object]:
    """Parse ``List-Unsubscribe`` into HTTP URLs and mailto entries.

    ``one_click`` is true when the sender declared RFC 8058 one-click
    (``List-Unsubscribe-Post: List-Unsubscribe=One-Click``) AND at
    least one HTTPS URI is present.
    """
    http_urls: list[str] = []
    mailtos: list[dict[str, str]] = []
    for raw in _split_list_unsubscribe(list_unsubscribe):
        uri = raw.strip().strip('<>').strip()
        if not uri:
            continue
        if uri.lower().startswith('mailto:'):
            mailtos.append(_parse_mailto(uri))
        elif uri.lower().startswith(('http://', 'https://')):
            http_urls.append(uri)
    one_click = 'one-click' in list_unsubscribe_post.lower() and any(
        u.lower().startswith('https://') for u in http_urls
    )
    return {
        'http_urls': http_urls,
        'mailtos': mailtos,
        'one_click': one_click,
    }


def _split_list_unsubscribe(header: str) -> list[str]:
    """Split a ``List-Unsubscribe`` value into its bracketed entries.

    The header is a comma-separated list of ``<uri>`` tokens, but
    mailto URIs may contain literal commas inside a ``body=`` or
    ``subject=`` parameter. Splitting on angle brackets avoids that.
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in header:
        if ch == '<':
            depth += 1
            buf.append(ch)
        elif ch == '>':
            depth -= 1
            buf.append(ch)
            if depth <= 0:
                parts.append(''.join(buf))
                buf = []
        elif ch == ',' and depth == 0:
            if buf:
                parts.append(''.join(buf))
                buf = []
        else:
            buf.append(ch)
    tail = ''.join(buf).strip()
    if tail:
        parts.append(tail)
    return [p.strip() for p in parts if p.strip()]


def _parse_mailto(uri: str) -> dict[str, str]:
    """Return ``{address, subject, body}`` for a ``mailto:`` URI."""
    parsed = urllib.parse.urlparse(uri)
    address = parsed.path
    params = urllib.parse.parse_qs(parsed.query)
    subject = params.get('subject', [''])[0]
    body = params.get('body', [''])[0]
    return {
        'address': address,
        'subject': subject or 'unsubscribe',
        'body': body or 'unsubscribe',
    }


class GmailClient:
    """Thin wrapper around the Gmail REST API."""

    def __init__(
        self,
        credentials: google.oauth2.credentials.Credentials,
    ) -> None:
        self._service = googleapiclient.discovery.build(
            'gmail',
            'v1',
            credentials=credentials,
            cache_discovery=False,
        )

    def search(
        self, query: str, max_results: int = 25
    ) -> list[dict[str, str]]:
        """Return ``[{message_id, thread_id}, ...]`` for ``query``."""
        resp = (
            self._service.users()
            .messages()
            .list(userId='me', q=query, maxResults=max_results)
            .execute()
        )
        out: list[dict[str, str]] = []
        for item in resp.get('messages', []):
            out.append(
                {
                    'message_id': item['id'],
                    'thread_id': item['threadId'],
                }
            )
        return out

    def list_inbox_message_ids(self) -> set[str] | None:
        """Return every message_id currently labeled INBOX.

        Paginates over ``users.messages.list`` with ``q=in:inbox``.
        Returns ``None`` if pagination exceeds the page cap without
        finishing — callers should treat that as "unknown" and skip
        any cache-prune step that would otherwise drop real entries.
        """
        ids: set[str] = set()
        page_token: str | None = None
        for _ in range(_INBOX_LIST_PAGE_CAP):
            resp = (
                self._service.users()
                .messages()
                .list(
                    userId='me',
                    q='in:inbox',
                    maxResults=_INBOX_LIST_PAGE_SIZE,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in resp.get('messages', []):
                ids.add(item['id'])
            page_token = resp.get('nextPageToken')
            if not page_token:
                return ids
        LOGGER.warning(
            'inbox listing exceeded %d pages; skipping cache prune',
            _INBOX_LIST_PAGE_CAP,
        )
        return None

    def get_message(self, message_id: str) -> Message:
        """Fetch and normalize a single message."""
        raw = (
            self._service.users()
            .messages()
            .get(userId='me', id=message_id, format='full')
            .execute()
        )
        headers = _headers_to_dict(raw.get('payload', {}).get('headers', []))
        body = _extract_text_body(raw.get('payload', {}))
        thread_replied = self._thread_has_sent(raw['threadId'])
        internal_ms = int(raw.get('internalDate', 0) or 0)
        prior_correspondence = self._has_prior_correspondence(
            headers.get('from', ''), internal_ms // 1000
        )
        return Message(
            message_id=raw['id'],
            thread_id=raw['threadId'],
            rfc822_message_id=headers.get('message-id', ''),
            references=headers.get('references', ''),
            from_header=headers.get('from', ''),
            to_header=headers.get('to', ''),
            subject=headers.get('subject', ''),
            date=headers.get('date', ''),
            body=body,
            snippet=raw.get('snippet', ''),
            thread_replied=thread_replied,
            prior_correspondence=prior_correspondence,
            label_ids=tuple(raw.get('labelIds', [])),
            list_unsubscribe=headers.get('list-unsubscribe', ''),
            list_unsubscribe_post=headers.get('list-unsubscribe-post', ''),
            list_id=headers.get('list-id', ''),
        )

    def reply(
        self,
        *,
        message_id: str,
        body: str,
    ) -> str:
        """Send a threaded reply. Returns the sent message id."""
        original = self.get_message(message_id)
        to_addr = _reply_to_address(original.from_header)
        subject = original.subject
        if not subject.lower().startswith('re:'):
            subject = f'Re: {subject}'
        references = _build_references(
            original.references, original.rfc822_message_id
        )
        raw_b64 = _encode_reply(
            to=to_addr,
            subject=subject,
            body=body,
            in_reply_to=original.rfc822_message_id,
            references=references,
        )
        sent = (
            self._service.users()
            .messages()
            .send(
                userId='me',
                body={
                    'raw': raw_b64,
                    'threadId': original.thread_id,
                },
            )
            .execute()
        )
        LOGGER.info(
            'sent reply %s in thread %s',
            sent.get('id'),
            original.thread_id,
        )
        return sent['id']

    def archive_thread(self, thread_id: str) -> None:
        """Remove the INBOX label from the thread."""
        self._service.users().threads().modify(
            userId='me',
            id=thread_id,
            body={'removeLabelIds': ['INBOX']},
        ).execute()

    def trash_thread(self, thread_id: str) -> None:
        """Move the thread to Trash (recoverable for ~30 days)."""
        self._service.users().threads().trash(
            userId='me',
            id=thread_id,
        ).execute()

    def mark_read(self, message_id: str) -> None:
        """Remove the UNREAD label from the message."""
        self._service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'removeLabelIds': ['UNREAD']},
        ).execute()

    def send_unsubscribe_mail(
        self, *, to: str, subject: str, body: str
    ) -> str:
        """Send a standalone unsubscribe email.

        Used for the ``mailto:`` form of ``List-Unsubscribe``. Not a
        threaded reply — the destination is a bounce-style address
        that should be matched by its envelope To: alone.
        """
        raw_b64 = _encode_reply(
            to=to,
            subject=subject,
            body=body,
            in_reply_to='',
            references='',
        )
        sent = (
            self._service.users()
            .messages()
            .send(userId='me', body={'raw': raw_b64})
            .execute()
        )
        LOGGER.info('sent unsubscribe mail %s to %s', sent.get('id'), to)
        return sent['id']

    def send_html(
        self, *, to: tuple[str, ...], subject: str, html: str
    ) -> str:
        """Send a standalone HTML email to one or more recipients.

        Sent from the authenticated account. Returns the sent id.
        """
        raw_b64 = _encode_html(to=to, subject=subject, html=html)
        sent = (
            self._service.users()
            .messages()
            .send(userId='me', body={'raw': raw_b64})
            .execute()
        )
        LOGGER.info('sent report %s to %s', sent.get('id'), ', '.join(to))
        return sent['id']

    def _has_prior_correspondence(
        self, from_header: str, message_epoch_s: int
    ) -> bool:
        """True if this sender is an established contact.

        Either the user has sent mail to this address before the
        message, or the sender has an inbound history predating the
        relationship age cutoff. Queries run cheapest-first and stop
        at the first match, each an existence search capped at one
        result. A failed lookup degrades to ``False`` (treat as no
        known relationship) rather than aborting the fetch.
        """
        try:
            return any(
                self.search(q, max_results=1)
                for q in _relationship_queries(from_header, message_epoch_s)
            )
        except Exception:
            LOGGER.exception('prior-correspondence lookup failed')
            return False

    def _thread_has_sent(self, thread_id: str) -> bool:
        thread = (
            self._service.users()
            .threads()
            .get(userId='me', id=thread_id, format='minimal')
            .execute()
        )
        for msg in thread.get('messages', []):
            if 'SENT' in msg.get('labelIds', []):
                return True
        return False


def _headers_to_dict(
    headers: list[dict[str, str]],
) -> dict[str, str]:
    return {h['name'].lower(): h['value'] for h in headers}


def _extract_text_body(payload: dict[str, object]) -> str:
    """Walk a Gmail payload tree for the best text/plain body."""
    mime_type = payload.get('mimeType', '')
    body = payload.get('body') or {}
    data = body.get('data') if isinstance(body, dict) else None
    if mime_type == 'text/plain' and data:
        return _decode_b64url(data)
    parts = payload.get('parts') or []
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = _extract_text_body(part)
            if text:
                return text
    # Fallback: take any body data we find, even if HTML.
    if data:
        return _decode_b64url(data)
    return ''


def _decode_b64url(data: str) -> str:
    padded = data + '=' * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode(
            'utf-8', errors='replace'
        )
    except ValueError:
        return ''


def _reply_to_address(from_header: str) -> str:
    """Prefer the bare email address for the To: header."""
    _name, addr = email.utils.parseaddr(from_header)
    return addr or from_header


def _relationship_queries(
    from_header: str,
    message_epoch_s: int,
    min_age_days: int = _RELATIONSHIP_MIN_AGE_DAYS,
) -> list[str]:
    """Build the ordered relationship queries to test for a sender.

    Empty when there's no usable address or timestamp to search on.
    Ordered cheapest-signal-first so the caller can stop at the first
    match:

    1. ``in:sent to:<addr> before:<msg>`` — mail the user sent to this
       address *before* the message arrived (an outbound
       relationship). The ``before:`` bound keeps the agent's own
       decline replies and mailto unsubscribes — always sent after
       the message lands — from being mistaken for a relationship.
    2. ``from:<addr> before:<msg - min_age_days>`` — inbound mail
       predating the age cutoff: a long-standing contact, as opposed
       to a cold sequence compressed into days. Omitted when the
       cutoff would fall before the epoch.
    """
    _name, addr = email.utils.parseaddr(from_header)
    if not addr or message_epoch_s <= 0:
        return []
    queries = [f'in:sent to:{addr} before:{message_epoch_s}']
    cutoff = message_epoch_s - min_age_days * _SECONDS_PER_DAY
    if cutoff > 0:
        queries.append(f'from:{addr} before:{cutoff}')
    return queries


def _build_references(existing: str, message_id: str) -> str:
    parts = [p for p in existing.split() if p]
    if message_id and message_id not in parts:
        parts.append(message_id)
    return ' '.join(parts)


def _encode_reply(
    *,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str,
    references: str,
) -> str:
    msg = email.message.EmailMessage(policy=email.policy.default)
    msg['To'] = to
    msg['Subject'] = subject
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
    if references:
        msg['References'] = references
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _encode_html(*, to: tuple[str, ...], subject: str, html: str) -> str:
    msg = email.message.EmailMessage(policy=email.policy.default)
    msg['To'] = ', '.join(to)
    msg['Subject'] = subject
    msg.set_content(
        'This report is best viewed in an HTML-capable mail client.'
    )
    msg.add_alternative(html, subtype='html')
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()
