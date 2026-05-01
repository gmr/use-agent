"""Small pure-function helpers in ``use_agent.cli``."""

import argparse

import pytest

from use_agent import cli


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('30s', 30),
        ('15m', 900),
        ('2h', 7200),
        ('1d', 86400),
        ('900', 900),
        ('5', 5),
        ('15M', 900),  # case-insensitive
        (' 45s ', 45),  # stripped
    ],
)
def test_parse_interval_accepts(raw: str, expected: int) -> None:
    assert cli._parse_interval(raw) == expected


@pytest.mark.parametrize('raw', ['abc', '', '15y', '-1', '0', '1.5m', '10 m'])
def test_parse_interval_rejects(raw: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli._parse_interval(raw)


def test_parser_run_defaults_to_pretty_mode() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(['run'])
    from use_agent import reporter

    assert args.output is reporter.Mode.PRETTY
    assert args.daemon is False
    assert args.dry_run is False
    assert args.delete is False
    assert args.interval == cli.DEFAULT_INTERVAL_SECONDS
    assert args.query is None
    assert args.max_results is None


def test_parser_run_json_and_plain_are_mutually_exclusive() -> None:
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(['run', '--json', '--plain'])


def test_parser_daemon_interval_parses() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(['run', '--daemon', '--interval', '30m'])
    assert args.daemon is True
    assert args.interval == 1800
