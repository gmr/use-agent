"""Streamed text buffering and narration-log routing."""

import logging

import pytest

from use_agent import reporter


def test_plain_narration_logs_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    with caplog.at_level(logging.DEBUG, logger='use_agent.narration'):
        rpt.on_text('Searching inbox for unread messages.')

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO
    assert record.getMessage() == 'Searching inbox for unread messages.'


def test_narration_with_json_fence_demotes_to_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    text = (
        'The search returned no unread emails. Here are the results:\n\n'
        '```json\n{"results": []}\n```'
    )
    with caplog.at_level(logging.DEBUG, logger='use_agent.narration'):
        rpt.on_text(text)

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.DEBUG
    # Text is logged verbatim — no stripping at DEBUG since -v gates it.
    assert '```json' in record.getMessage()
    assert '"results"' in record.getMessage()


def test_buffer_keeps_text_verbatim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.JSON)
    fenced = (
        '```json\n{"results": [{"classification": "NOT_COLD_SALES"}]}\n```'
    )
    with caplog.at_level(logging.DEBUG, logger='use_agent.narration'):
        rpt.on_text(fenced)

    # finish() parses _buffer, so the fence must survive verbatim
    # regardless of how it got logged.
    assert rpt._buffer == [fenced]


def test_whitespace_only_does_not_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    with caplog.at_level(logging.DEBUG, logger='use_agent.narration'):
        rpt.on_text('   \n\n   ')
    assert not caplog.records


def test_empty_results_logs_info_and_exits_zero(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    rpt.on_text('```json\n{"results": []}\n```')
    with caplog.at_level(logging.INFO, logger='use_agent.reporter'):
        rc = rpt.finish()

    assert rc == 0
    assert any(
        r.levelno == logging.INFO
        and r.getMessage() == 'No new unread emails to process'
        for r in caplog.records
    )
    # Nothing to stdout in pretty mode when there are no rows.
    assert capsys.readouterr().out == ''


def test_missing_summary_logs_error_and_exits_one(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    rpt.on_text('No JSON block here, just prose.')
    with caplog.at_level(logging.ERROR, logger='use_agent.reporter'):
        rc = rpt.finish()

    assert rc == 1
    assert any(
        r.levelno == logging.ERROR and 'no summary' in r.getMessage()
        for r in caplog.records
    )
    assert capsys.readouterr().out == ''


def test_plain_mode_empty_results_logs_info(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PLAIN)
    rpt.on_text('```json\n{"results": []}\n```')
    with caplog.at_level(logging.INFO, logger='use_agent.reporter'):
        rc = rpt.finish()

    assert rc == 0
    assert any(
        r.getMessage() == 'No new unread emails to process'
        for r in caplog.records
    )
    assert capsys.readouterr().out == ''
