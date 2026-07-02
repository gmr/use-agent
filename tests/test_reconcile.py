"""Tests for summary reconciliation into the action store."""

import sqlite3

from use_agent import agent, storage


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute('SELECT * FROM actions')]
    finally:
        conn.close()


def test_reconcile_inserts_missing_acted_rows(tmp_path):
    db = tmp_path / 'actions.db'
    store = storage.Store(db, query_target='inbox')
    summary = [
        {
            'message_id': 'm1',
            'sender': 'Jane <jane@vendor.io>',
            'subject': 'Quick call?',
            'date': 'Wed, 02 Jul 2025 09:30:00 -0400',
            'classification': 'COLD_SALES',
            'category': 'Recruiter',
            'score': 95,
            'response_mode': 'hard_remove',
            'action_taken': 'Reply sent & archived',
        },
        {
            'message_id': 'm2',
            'sender': 'noreply@news.io',
            'subject': 'Weekly digest',
            'date': '',
            'classification': 'NOT_COLD_SALES',
            'response_mode': 'none',
            'action_taken': 'Skipped (not cold sales)',
        },
    ]
    agent._reconcile_history(store, summary)
    store.close()

    rows = _rows(db)
    # Only the acted-upon row is inserted; the skipped row is dropped.
    assert len(rows) == 1
    assert rows[0]['message_id'] == 'm1'
    assert rows[0]['source'] == 'summary'
    assert rows[0]['sent_at'] == '2025-07-02T13:30:00+00:00'
    assert rows[0]['category'] == 'Recruiter'


def test_reconcile_skips_rows_already_recorded_by_tool(tmp_path):
    db = tmp_path / 'actions.db'
    store = storage.Store(db, query_target='inbox')
    # The tool recorded m1 during the run.
    store.record(
        sender='Jane <jane@vendor.io>',
        subject='Quick call?',
        sent_at='Wed, 02 Jul 2025 09:30:00 -0400',
        classification='COLD_SALES',
        response_mode='hard_remove',
        action_taken='Reply sent & archived',
        score=95,
        message_id='m1',
        source='tool',
    )
    summary = [
        {
            'message_id': 'm1',
            'sender': 'Jane <jane@vendor.io>',
            'subject': 'Quick call?',
            'date': 'Wed, 02 Jul 2025 09:30:00 -0400',
            'classification': 'COLD_SALES',
            'response_mode': 'hard_remove',
            'action_taken': 'Reply sent & archived',
            'score': 95,
        }
    ]
    agent._reconcile_history(store, summary)
    store.close()

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]['source'] == 'tool'


def test_reconcile_tolerates_empty_summary(tmp_path):
    store = storage.Store(tmp_path / 'actions.db', query_target='inbox')
    try:
        agent._reconcile_history(store, None)
        agent._reconcile_history(store, [])
    finally:
        store.close()
    assert _rows(tmp_path / 'actions.db') == []
