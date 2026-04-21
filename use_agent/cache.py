"""JSON-backed cache of Gmail message_ids already processed.

Entries live in the cache until the underlying message leaves the
Gmail inbox, at which point :meth:`Cache.retain` drops them. The
``search`` tool consults this cache so the agent never wastes a turn
re-investigating a message it has already examined.
"""

import dataclasses
import datetime
import json
import logging
import pathlib
import typing

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class Cache:
    """Set of seen Gmail message_ids, persisted as JSON on disk."""

    path: pathlib.Path
    _entries: dict[str, str] = dataclasses.field(default_factory=dict)

    @classmethod
    def load(cls, path: pathlib.Path) -> typing.Self:
        """Load the cache at ``path``.

        A missing, empty, or corrupt file yields an empty cache —
        correctness is preserved (missed entries just cause the next
        run to re-examine a message, never to double-send a reply).
        """
        instance = cls(path=path)
        if not path.exists():
            return instance
        try:
            raw = path.read_text(encoding='utf-8')
            data = json.loads(raw) if raw.strip() else {}
        except OSError, json.JSONDecodeError:
            LOGGER.warning('cache at %s is unreadable; starting empty', path)
            return instance
        if isinstance(data, dict):
            instance._entries = {
                str(k): str(v) for k, v in data.items() if isinstance(k, str)
            }
        return instance

    def save(self) -> None:
        """Write the cache to :attr:`path` (creates parents as needed)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._entries, indent=2, sort_keys=True),
            encoding='utf-8',
        )

    def __contains__(self, message_id: object) -> bool:
        return isinstance(message_id, str) and message_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, message_id: str) -> None:
        """Mark ``message_id`` as seen. Idempotent — re-adds are no-ops."""
        if message_id not in self._entries:
            self._entries[message_id] = datetime.datetime.now(
                datetime.UTC
            ).isoformat()

    def retain(self, keep: typing.Iterable[str]) -> int:
        """Drop entries whose id is not in ``keep``.

        Returns the number of entries dropped. Used at run start to
        evict message_ids no longer present in the inbox.
        """
        keep_set = set(keep)
        before = len(self._entries)
        self._entries = {
            k: v for k, v in self._entries.items() if k in keep_set
        }
        return before - len(self._entries)
