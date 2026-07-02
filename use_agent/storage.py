"""SQLite-backed history of the actions use-agent took.

One row per acted-upon message. Rows are written primarily by the
``record_action`` tool at action time; :func:`use_agent.agent.run`
then reconciles the store against the agent's final JSON summary and
inserts any acted-upon row the model forgot to record (tagged
``source='summary'``).

The database is opened in WAL mode with a busy timeout so a daemon
and an ad-hoc run can write concurrently on a *local* filesystem.
WAL is not safe over a network mount (NFS/SMB) — don't point
``USE_AGENT_DB`` at one from more than one host.
"""

import dataclasses
import datetime
import email.utils
import logging
import pathlib
import sqlite3
import typing

LOGGER = logging.getLogger(__name__)

# Wait on a peer's write lock rather than raising "database is locked".
_BUSY_TIMEOUT_MS: int = 5000

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS actions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    processed_at   TEXT    NOT NULL,
    query_target   TEXT    NOT NULL,
    sender         TEXT    NOT NULL,
    subject        TEXT    NOT NULL,
    sent_at        TEXT,
    classification TEXT    NOT NULL,
    category       TEXT,
    response_mode  TEXT    NOT NULL,
    action_taken   TEXT    NOT NULL,
    score          INTEGER,
    message_id     TEXT,
    source         TEXT    NOT NULL DEFAULT 'tool'
);
CREATE INDEX IF NOT EXISTS idx_actions_processed_at
    ON actions (processed_at);
"""

_INSERT: str = """
INSERT INTO actions (
    processed_at, query_target, sender, subject, sent_at,
    classification, category, response_mode, action_taken, score,
    message_id, source
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def query_target(query: str) -> str:
    """Derive a short target label ('inbox', 'spam', ...) from a query.

    Reads the first ``in:`` or ``label:`` operand in the Gmail search
    query. Falls back to ``'inbox'`` — the default query use-agent
    builds always searches the inbox.
    """
    for token in query.split():
        low = token.lower()
        for prefix in ('in:', 'label:'):
            if low.startswith(prefix):
                target = token[len(prefix) :].strip()
                if target:
                    return target.lower()
    return 'inbox'


def is_action(action_taken: str) -> bool:
    """True when ``action_taken`` reflects a real mailbox mutation.

    Excludes the ``Skipped (...)`` and ``Dry-run: ...`` variants so
    reconciliation only ever persists messages that were acted upon.
    """
    text = action_taken.strip()
    if not text:
        return False
    return not text.startswith(('Skipped', 'Dry-run'))


def normalize_date(raw: str) -> str | None:
    """Normalize an RFC 2822 ``Date`` header to ISO 8601 UTC.

    Returns ``None`` for an empty value, or the raw string unchanged
    when it can't be parsed — a malformed date shouldn't lose the row.
    """
    value = (raw or '').strip()
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except TypeError, ValueError:
        return value
    if parsed is None:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC).isoformat()


def _coerce_score(score: object) -> int | None:
    try:
        return int(typing.cast('typing.SupportsInt', score))
    except TypeError, ValueError:
        return None


@dataclasses.dataclass(slots=True)
class Store:
    """Append-only writer for the action-history database."""

    path: pathlib.Path
    query_target: str
    _conn: sqlite3.Connection = dataclasses.field(init=False)
    _recorded: set[str] = dataclasses.field(default_factory=set)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path, timeout=_BUSY_TIMEOUT_MS / 1000
        )
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute(f'PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}')
        self._conn.executescript(_SCHEMA)

    @staticmethod
    def _key(message_id: str, sender: str, subject: str) -> str:
        return message_id or f'{sender}\x00{subject}'

    def has(self, *, message_id: str, sender: str, subject: str) -> bool:
        """True if this message was already recorded during this run."""
        return self._key(message_id, sender, subject) in self._recorded

    def record(
        self,
        *,
        sender: str,
        subject: str,
        sent_at: str,
        classification: str,
        response_mode: str,
        action_taken: str,
        category: str = '',
        score: object = None,
        message_id: str = '',
        source: str = 'tool',
    ) -> None:
        """Insert one acted-upon message. ``processed_at`` is now (UTC)."""
        processed_at = datetime.datetime.now(datetime.UTC).isoformat()
        with self._conn:
            self._conn.execute(
                _INSERT,
                (
                    processed_at,
                    self.query_target,
                    sender,
                    subject,
                    normalize_date(sent_at),
                    classification,
                    category or None,
                    response_mode,
                    action_taken,
                    _coerce_score(score),
                    message_id or None,
                    source,
                ),
            )
        self._recorded.add(self._key(message_id, sender, subject))
        LOGGER.debug(
            'recorded action (%s): %s / %s', source, sender, action_taken
        )

    def close(self) -> None:
        self._conn.close()
