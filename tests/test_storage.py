"""Tests for the SQLite action-history store."""

import sqlite3

from use_agent import storage


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute('SELECT * FROM actions')]
    finally:
        conn.close()


def test_query_target_defaults_to_inbox():
    assert storage.query_target('is:unread -from:example.com') == 'inbox'


def test_query_target_reads_in_operand():
    assert storage.query_target('in:spam is:unread') == 'spam'


def test_query_target_reads_label_operand():
    assert storage.query_target('label:Promotions newer_than:7d') == (
        'promotions'
    )


def test_is_action_true_for_real_actions():
    assert storage.is_action('Reply sent & archived')
    assert storage.is_action('Unsubscribed & trashed (one_click_post)')
    assert storage.is_action('Trashed')
    assert storage.is_action('Error: boom')


def test_is_action_false_for_skips_and_dry_run():
    assert not storage.is_action('Skipped (not cold sales)')
    assert not storage.is_action('Dry-run: would trash')
    assert not storage.is_action('')


def test_normalize_date_rfc2822_to_utc_iso():
    out = storage.normalize_date('Wed, 02 Jul 2025 09:30:00 -0400')
    assert out == '2025-07-02T13:30:00+00:00'


def test_normalize_date_naive_assumed_utc():
    out = storage.normalize_date('Wed, 02 Jul 2025 09:30:00')
    assert out == '2025-07-02T09:30:00+00:00'


def test_normalize_date_empty_is_none():
    assert storage.normalize_date('') is None
    assert storage.normalize_date('   ') is None


def test_normalize_date_unparseable_returns_raw():
    assert storage.normalize_date('not a date') == 'not a date'


def test_record_persists_row(tmp_path):
    db = tmp_path / 'actions.db'
    store = storage.Store(db, query_target='inbox')
    store.record(
        sender='Jane <jane@vendor.io>',
        subject='Quick call?',
        sent_at='Wed, 02 Jul 2025 09:30:00 -0400',
        classification='COLD_SALES',
        category='Staff Augmentation',
        response_mode='hard_remove',
        action_taken='Reply sent & archived',
        score=95,
        message_id='abc123',
    )
    store.close()

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row['query_target'] == 'inbox'
    assert row['sender'] == 'Jane <jane@vendor.io>'
    assert row['sent_at'] == '2025-07-02T13:30:00+00:00'
    assert row['classification'] == 'COLD_SALES'
    assert row['category'] == 'Staff Augmentation'
    assert row['action_taken'] == 'Reply sent & archived'
    assert row['score'] == 95
    assert row['message_id'] == 'abc123'
    assert row['source'] == 'tool'
    assert row['processed_at']


def test_record_uses_wal(tmp_path):
    db = tmp_path / 'actions.db'
    store = storage.Store(db, query_target='inbox')
    try:
        conn = sqlite3.connect(db)
        mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
        conn.close()
        assert mode.lower() == 'wal'
    finally:
        store.close()


def test_bad_score_coerced_to_null(tmp_path):
    db = tmp_path / 'actions.db'
    store = storage.Store(db, query_target='inbox')
    store.record(
        sender='x',
        subject='y',
        sent_at='',
        classification='BULK_MARKETING',
        response_mode='delete',
        action_taken='Trashed',
        score='not-an-int',
    )
    store.close()
    rows = _rows(db)
    assert rows[0]['score'] is None
    assert rows[0]['sent_at'] is None
    assert rows[0]['message_id'] is None
    assert rows[0]['category'] is None


def test_has_tracks_recorded_within_run(tmp_path):
    store = storage.Store(tmp_path / 'actions.db', query_target='inbox')
    try:
        assert not store.has(message_id='m1', sender='a', subject='b')
        store.record(
            sender='a',
            subject='b',
            sent_at='',
            classification='COLD_SALES',
            response_mode='hard_remove',
            action_taken='Trashed',
            message_id='m1',
        )
        assert store.has(message_id='m1', sender='a', subject='b')
    finally:
        store.close()


def test_has_falls_back_to_sender_subject_without_id(tmp_path):
    store = storage.Store(tmp_path / 'actions.db', query_target='inbox')
    try:
        store.record(
            sender='a@x.io',
            subject='Hi',
            sent_at='',
            classification='COLD_SALES',
            response_mode='hard_remove',
            action_taken='Trashed',
            message_id='',
        )
        assert store.has(message_id='', sender='a@x.io', subject='Hi')
        assert not store.has(message_id='', sender='a@x.io', subject='Bye')
    finally:
        store.close()
