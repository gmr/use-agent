"""Streamed text buffering and narration-log stripping."""

import logging

import pytest

from use_agent import reporter


def test_on_text_strips_json_fence_from_narration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    text = (
        'Classifying the message now.\n\n'
        '```json\n'
        '{"results": [{"classification": "NOT_COLD_SALES"}]}\n'
        '```\n\n'
        'Summary: 1 message processed.'
    )
    with caplog.at_level(logging.INFO, logger='use_agent.narration'):
        rpt.on_text(text)

    logged = '\n'.join(r.getMessage() for r in caplog.records)
    assert '```json' not in logged
    assert '"results"' not in logged
    assert '"NOT_COLD_SALES"' not in logged
    assert 'Classifying the message now.' in logged
    assert 'Summary: 1 message processed.' in logged


def test_on_text_buffers_full_text_including_fence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.JSON)
    fenced = (
        '```json\n{"results": [{"classification": "NOT_COLD_SALES"}]}\n```'
    )
    with caplog.at_level(logging.INFO, logger='use_agent.narration'):
        rpt.on_text(fenced)

    # Buffer must still contain the fenced block so finish() can parse
    # it; stripping happens only at the narration logger.
    assert '\n'.join(rpt._buffer) == fenced


def test_on_text_plain_text_narrates_normally(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    with caplog.at_level(logging.INFO, logger='use_agent.narration'):
        rpt.on_text('Searching inbox for unread messages.')

    logged = '\n'.join(r.getMessage() for r in caplog.records)
    assert logged == 'Searching inbox for unread messages.'


def test_on_text_whitespace_only_does_not_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    with caplog.at_level(logging.INFO, logger='use_agent.narration'):
        rpt.on_text('   \n\n   ')
    assert not caplog.records


def test_on_text_fence_only_does_not_log_but_buffers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rpt = reporter.Reporter(mode=reporter.Mode.PRETTY)
    fenced = '```json\n{"results": []}\n```'
    with caplog.at_level(logging.INFO, logger='use_agent.narration'):
        rpt.on_text(fenced)
    # Whole payload was JSON; narration has nothing to say.
    assert not caplog.records
    # But the buffer kept it for finish().
    assert rpt._buffer == [fenced]
