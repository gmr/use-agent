"""Aggregate the action history into a weekly HTML report.

Reads rows from the SQLite store written by
:mod:`use_agent.storage`, rolls them up into the view model the
``report.html.j2`` template expects, and renders standalone
inline-styled HTML suitable for emailing.

The window is filtered on ``processed_at`` (when use-agent acted).
With no explicit ``days``/``since`` it covers the previous full
calendar week (last Monday through Sunday); ``days`` selects a
trailing window ending today instead. Timestamps are grouped in the
local timezone so "Activity by day" lines up with the reader's
calendar.
"""

import collections
import datetime
import email.utils
import html
import logging
import pathlib
import sqlite3
import typing

import jinja2

from use_agent import config

LOGGER = logging.getLogger(__name__)

# Palette lifted from the approved Weekly Report design.
_COLD_TYPE = 'Cold sales'
_BULK_TYPE = 'Bulk marketing'
_COLD_COLOR = '#8a5a30'
_COLD_BG = '#f4ebe1'
_BULK_COLOR = '#3f5f80'
_BULK_BG = '#e7edf3'

_TOP_OFFENDERS = 10
_TOP_THEMES = 8

_EN_DASH = '\u2013'  # en dash; matches the report design date range

# response_mode → human label for the "How each was handled" section.
_RESPONSE_LABELS: dict[str, str] = {
    'unsubscribe_and_delete': 'Unsubscribed & deleted',
    'delete': 'Deleted',
    'hard_remove': 'Removed (remove request)',
    'hard_remove_with_correction': 'Removed + correction reply',
    'specific_decline': 'Declined with reply',
    'none': 'No action needed',
}


def render(
    db_path: pathlib.Path,
    *,
    days: int | None = None,
    since: datetime.date | None = None,
    now: datetime.datetime | None = None,
) -> str:
    """Render the weekly report to a standalone HTML string.

    With neither ``days`` nor ``since`` the window is the previous
    full calendar week (last Monday through Sunday). ``days`` selects
    a trailing window of the last N calendar days ending today;
    ``since`` starts the window on a specific date and ends today.
    Either override makes "Activity by day" span exactly that window.
    ``now`` is injectable for deterministic tests; it defaults to the
    current local time.
    """
    now = now or datetime.datetime.now().astimezone()
    today = now.astimezone().date()
    if since is not None:
        start_date, end_date = since, today
    elif days is not None:
        start_date = today - datetime.timedelta(days=days - 1)
        end_date = today
    else:
        this_monday = today - datetime.timedelta(days=today.weekday())
        start_date = this_monday - datetime.timedelta(days=7)
        end_date = this_monday - datetime.timedelta(days=1)
    start = datetime.datetime.combine(
        start_date, datetime.time.min
    ).astimezone()
    end = datetime.datetime.combine(end_date, datetime.time.max).astimezone()
    rows = _fetch_rows(db_path, start, end)
    context = _build_context(rows, start=start, end=end, generated=now)
    return (
        _environment()
        .get_template(config.REPORT_TEMPLATE.name)
        .render(context)
    )


def _environment() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(config.TEMPLATES_DIR)),
        autoescape=True,  # report data (sender names, subjects) is untrusted
        undefined=jinja2.StrictUndefined,
    )


def _fetch_rows(
    db_path: pathlib.Path,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[dict[str, typing.Any]]:
    """Return action rows with ``processed_at`` within ``[start, end]``.

    A missing database or table yields an empty report rather than an
    error — the user may simply not have run the agent yet.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            'SELECT * FROM actions WHERE processed_at >= ? '
            'AND processed_at <= ? ORDER BY processed_at',
            (
                start.astimezone(datetime.UTC).isoformat(),
                end.astimezone(datetime.UTC).isoformat(),
            ),
        )
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        LOGGER.warning('no actions table at %s; empty report', db_path)
        return []
    finally:
        conn.close()


def _build_context(
    rows: list[dict[str, typing.Any]],
    *,
    start: datetime.datetime,
    end: datetime.datetime,
    generated: datetime.datetime | None = None,
) -> dict[str, typing.Any]:
    total = len(rows)
    return {
        'headline': f'{total} unwanted emails handled for you',
        'weekRange': _format_range(start, end),
        'inboxLine': _inbox_line(rows),
        'stats': _stats(rows),
        'cls': _classification(rows),
        'days': _days(rows, start=start, end=end),
        'responses': _responses(rows),
        'offenders': _offenders(rows),
        'themes': _themes(rows),
        'footerLine': (
            'USE Agent (Unsolicited Sales Email) reviews your inbox and '
            'spam folder periodically, auto-responds to cold outreach, '
            'unsubscribes you from bulk marketing, and clears it out.'
        ),
        'generatedAt': _format_stamp(generated or end),
    }


def _bar_width(count: int, max_count: int) -> str:
    if max_count <= 0:
        return '0%'
    return f'{round(count / max_count * 100)}%'


def _bars(
    pairs: list[tuple[str, int]],
) -> list[dict[str, typing.Any]]:
    """Turn ``(name, count)`` pairs into rows with a relative bar width.

    Shared by the day / response / theme sections, which are identical
    bar charts differing only in their source data.
    """
    max_count = max((n for _, n in pairs), default=0)
    return [
        {'name': name, 'count': n, 'width': _bar_width(n, max_count)}
        for name, n in pairs
    ]


def _action_matches(rows: list[dict[str, typing.Any]], needle: str) -> int:
    return sum(1 for r in rows if needle in (r['action_taken'] or ''))


def _stats(rows: list[dict[str, typing.Any]]) -> list[dict[str, str]]:
    removed = sum(
        1 for r in rows if 'trashed' in (r['action_taken'] or '').lower()
    )
    return [
        {'value': str(len(rows)), 'label': 'Actions taken'},
        {'value': str(removed), 'label': 'Emails removed'},
        {
            'value': str(_action_matches(rows, 'Unsubscribed')),
            'label': 'Unsubscribes sent',
        },
        {
            'value': str(_action_matches(rows, 'Reply sent')),
            'label': 'Replies sent',
        },
    ]


def _classification(
    rows: list[dict[str, typing.Any]],
) -> dict[str, typing.Any]:
    total = len(rows)
    cold = sum(1 for r in rows if r['classification'] == 'COLD_SALES')
    bulk = sum(1 for r in rows if r['classification'] == 'BULK_MARKETING')

    def pct(n: int) -> int:
        return round(n / total * 100) if total else 0

    return {
        'coldCount': cold,
        'bulkCount': bulk,
        'coldWidth': f'{pct(cold)}%',
        'bulkWidth': f'{pct(bulk)}%',
        'coldNote': f'{pct(cold)}% · declined or removed',
        'bulkNote': f'{pct(bulk)}% · unsubscribed & cleared',
    }


def _inbox_line(rows: list[dict[str, typing.Any]]) -> str:
    counts = collections.Counter(r['query_target'] for r in rows)
    if not counts:
        return 'nothing to report yet'
    parts = [f'{n} from {target}' for target, n in counts.most_common()]
    return ', '.join(parts)


def _days(
    rows: list[dict[str, typing.Any]],
    *,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[dict[str, typing.Any]]:
    counts: dict[datetime.date, int] = collections.defaultdict(int)
    for r in rows:
        stamp = _parse(r['processed_at'])
        if stamp is not None:
            counts[stamp.astimezone().date()] += 1
    day = start.date()
    last = end.date()
    pairs: list[tuple[str, int]] = []
    while day <= last:
        pairs.append((day.strftime('%a'), counts.get(day, 0)))
        day += datetime.timedelta(days=1)
    return _bars(pairs)


def _responses(
    rows: list[dict[str, typing.Any]],
) -> list[dict[str, typing.Any]]:
    counts = collections.Counter(r['response_mode'] for r in rows)
    return _bars(
        [
            (_RESPONSE_LABELS.get(mode or '', mode or 'Other'), n)
            for mode, n in counts.most_common()
        ]
    )


def _offenders(
    rows: list[dict[str, typing.Any]],
) -> list[dict[str, typing.Any]]:
    grouped: dict[str, dict[str, typing.Any]] = {}
    for r in rows:
        # Some senders arrive already HTML-escaped ('&lt;a@b.com&gt;');
        # unescape first so parseaddr sees real angle brackets and the
        # template's autoescape encodes them exactly once.
        sender = html.unescape(r['sender'] or '')
        name, addr = email.utils.parseaddr(sender)
        key = (addr or sender).lower()
        if not key:
            continue
        entry = grouped.setdefault(
            key,
            {
                'name': name or addr or key,
                'addr': addr or key,
                'count': 0,
                'classes': collections.Counter(),
            },
        )
        entry['count'] += 1
        entry['classes'][r['classification']] += 1
    ranked = sorted(grouped.values(), key=lambda e: e['count'], reverse=True)[
        :_TOP_OFFENDERS
    ]
    out: list[dict[str, typing.Any]] = []
    for e in ranked:
        dominant = e['classes'].most_common(1)[0][0]
        is_cold = dominant == 'COLD_SALES'
        out.append(
            {
                'name': e['name'],
                'addr': e['addr'],
                'count': e['count'],
                'type': _COLD_TYPE if is_cold else _BULK_TYPE,
                'typeColor': _COLD_COLOR if is_cold else _BULK_COLOR,
                'typeBg': _COLD_BG if is_cold else _BULK_BG,
            }
        )
    return out


def _themes(
    rows: list[dict[str, typing.Any]],
) -> list[dict[str, typing.Any]]:
    counts = collections.Counter(
        (r['category'] or 'Uncategorized') for r in rows
    )
    return _bars(counts.most_common(_TOP_THEMES))


def _parse(stamp: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(stamp)
    except TypeError, ValueError:
        return None


def _format_range(start: datetime.datetime, end: datetime.datetime) -> str:
    if start.year == end.year:
        left = start.strftime('%b %-d')
    else:
        left = start.strftime('%b %-d, %Y')
    return f'{left} {_EN_DASH} {end.strftime("%b %-d, %Y")}'


def _format_stamp(when: datetime.datetime) -> str:
    return when.strftime('%b %-d, %Y · %-I:%M %p')
