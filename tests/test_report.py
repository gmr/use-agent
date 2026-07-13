"""Tests for weekly report aggregation and rendering."""

import datetime

from use_agent import report, storage

_NOW = datetime.datetime(2026, 6, 30, 6, 0, tzinfo=datetime.UTC)
# render() with days=7 starts at midnight 6 days before end's date.
_START = datetime.datetime(2026, 6, 24, 0, 0, tzinfo=datetime.UTC)


def _row(**overrides):
    base = {
        'processed_at': '2026-06-28T12:00:00+00:00',
        'query_target': 'inbox',
        'sender': 'Growth Partners <outreach@growthpartners.io>',
        'subject': 'Quick call?',
        'sent_at': None,
        'classification': 'COLD_SALES',
        'category': 'Sales outreach',
        'response_mode': 'hard_remove',
        'action_taken': 'Reply sent & archived',
        'score': 90,
        'message_id': 'm',
        'source': 'tool',
    }
    base.update(overrides)
    return base


def _ctx(rows):
    return report._build_context(rows, start=_START, end=_NOW)


def test_headline_and_totals():
    ctx = _ctx([_row(), _row(), _row()])
    assert ctx['headline'] == '3 unwanted emails handled for you'
    assert ctx['stats'][0] == {'value': '3', 'label': 'Actions taken'}


def test_stats_counts_by_action():
    rows = [
        _row(action_taken='Reply sent & archived'),
        _row(action_taken='Unsubscribed & trashed (one_click_post)'),
        _row(action_taken='Trashed'),
        _row(action_taken='Reply sent & trashed'),
    ]
    stats = {s['label']: s['value'] for s in _ctx(rows)['stats']}
    assert stats['Actions taken'] == '4'
    # Removed = anything trashed (unsub+trash, trash, reply+trash).
    assert stats['Emails removed'] == '3'
    assert stats['Unsubscribes sent'] == '1'
    assert stats['Replies sent'] == '2'


def test_classification_split():
    rows = [
        _row(classification='COLD_SALES'),
        _row(classification='BULK_MARKETING'),
        _row(classification='BULK_MARKETING'),
        _row(classification='BULK_MARKETING'),
    ]
    cls = _ctx(rows)['cls']
    assert cls['coldCount'] == 1
    assert cls['bulkCount'] == 3
    assert cls['coldWidth'] == '25%'
    assert cls['bulkWidth'] == '75%'


def test_inbox_line_groups_by_target():
    rows = [
        _row(query_target='inbox'),
        _row(query_target='inbox'),
        _row(query_target='spam'),
    ]
    assert _ctx(rows)['inboxLine'] == '2 from inbox, 1 from spam'


def test_offenders_ranked_with_type():
    rows = [
        _row(
            sender='Growth <a@growth.io>',
            classification='COLD_SALES',
        ),
        _row(
            sender='Growth <a@growth.io>',
            classification='COLD_SALES',
        ),
        _row(
            sender='Deals <b@deals.com>',
            classification='BULK_MARKETING',
        ),
    ]
    offenders = _ctx(rows)['offenders']
    assert offenders[0]['addr'] == 'a@growth.io'
    assert offenders[0]['count'] == 2
    assert offenders[0]['type'] == 'Cold sales'
    assert offenders[1]['addr'] == 'b@deals.com'
    assert offenders[1]['type'] == 'Bulk marketing'


def test_themes_ranked_and_uncategorized():
    rows = [
        _row(category='Recruiter'),
        _row(category='Recruiter'),
        _row(category=None),
    ]
    themes = {t['name']: t['count'] for t in _ctx(rows)['themes']}
    assert themes['Recruiter'] == 2
    assert themes['Uncategorized'] == 1


def test_responses_labelled():
    rows = [
        _row(response_mode='unsubscribe_and_delete'),
        _row(response_mode='delete'),
        _row(response_mode='delete'),
    ]
    responses = {r['name']: r['count'] for r in _ctx(rows)['responses']}
    assert responses['Deleted'] == 2
    assert responses['Unsubscribed & deleted'] == 1


def test_days_span_window_and_sum_to_total():
    rows = [_row(), _row(), _row()]
    days = _ctx(rows)['days']
    # a 7-calendar-day window is exactly 7 rows, start..end inclusive.
    assert len(days) == 7
    assert sum(d['count'] for d in days) == 3


def test_render_full_html(tmp_path):
    db = tmp_path / 'actions.db'
    store = storage.Store(db, query_target='inbox')
    store.record(
        sender='Growth Partners <outreach@growthpartners.io>',
        subject='Quick call?',
        sent_at='',
        classification='COLD_SALES',
        category='Sales outreach',
        response_mode='hard_remove',
        action_taken='Reply sent & archived',
        message_id='m1',
    )
    store.close()

    # Rows are stamped at record time (real now); render with a
    # matching now so the trailing window includes them.
    html = report.render(db, days=7, now=datetime.datetime.now().astimezone())
    assert html.startswith('<!DOCTYPE html>')
    assert '1 unwanted emails handled for you' in html
    assert 'outreach@growthpartners.io' in html
    assert 'Sales outreach' in html


def test_render_missing_db_is_empty_report(tmp_path):
    html = report.render(tmp_path / 'nope.db', days=7, now=_NOW)
    assert '0 unwanted emails handled for you' in html


def test_render_defaults_to_previous_full_week(tmp_path):
    # Tue Jun 30 2026 -> previous Mon-Sun is Jun 22 through Jun 28.
    now = datetime.datetime(2026, 6, 30, 6, 0).astimezone()
    html = report.render(tmp_path / 'nope.db', now=now)
    assert f'Jun 22 {report._EN_DASH} Jun 28, 2026' in html


def test_offenders_unescape_double_escaped_sender():
    rows = [
        _row(sender='jim jachetta &lt;jim@vidovation.com&gt;'),
        _row(sender='jim jachetta &lt;jim@vidovation.com&gt;'),
    ]
    offender = _ctx(rows)['offenders'][0]
    assert offender['name'] == 'jim jachetta'
    assert offender['addr'] == 'jim@vidovation.com'


def test_render_escapes_sender(tmp_path):
    db = tmp_path / 'actions.db'
    store = storage.Store(db, query_target='inbox')
    store.record(
        sender='<script>x</script> <evil@x.io>',
        subject='hi',
        sent_at='',
        classification='COLD_SALES',
        response_mode='delete',
        action_taken='Trashed',
        message_id='m1',
    )
    store.close()
    html = report.render(db, days=7, now=datetime.datetime.now().astimezone())
    assert '<script>x</script>' not in html
    assert '&lt;script&gt;' in html
