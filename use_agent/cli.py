"""Command-line entry point for use-agent."""

import argparse
import asyncio
import datetime
import logging
import pathlib
import re
import sys

from use_agent import agent, auth, config, gmail, report, reporter
from use_agent import settings as settings_mod

LOGGER = logging.getLogger(__name__)

_INTERVAL_RE = re.compile(r'^(?P<n>\d+)(?P<unit>[smhd]?)$')
_UNIT_SECONDS = {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}
DEFAULT_INTERVAL_SECONDS = 15 * 60


def _parse_interval(raw: str) -> int:
    match = _INTERVAL_RE.fullmatch(raw.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(
            f'invalid interval {raw!r}; use e.g. 30s, 15m, 2h, 1d'
        )
    seconds = int(match.group('n')) * _UNIT_SECONDS[match.group('unit')]
    if seconds <= 0:
        raise argparse.ArgumentTypeError(
            f'interval must be positive, got {raw!r}'
        )
    return seconds


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='use-agent',
        description=('Claude Agent that triages cold sales email in Gmail.'),
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='enable debug logging',
    )
    parser.add_argument(
        '--logfile',
        type=pathlib.Path,
        default=None,
        help='append structured logs to this file (in addition to stderr)',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser(
        'auth',
        help='run the Gmail OAuth flow and save credentials',
    )

    run_p = sub.add_parser('run', help='process the inbox once')
    run_p.add_argument(
        '--query',
        default=None,
        help=(
            'override the Gmail search query; defaults to the one '
            'in config.toml (or one built from the safelist)'
        ),
    )
    run_p.add_argument(
        '--max',
        dest='max_results',
        type=int,
        default=None,
        help='override maximum candidates to examine',
    )
    run_p.add_argument(
        '--lookback',
        default=None,
        help=(
            'limit how far back messages are considered, as a Gmail '
            'newer_than operand: <N>d, <N>m, or <N>y (e.g. 7d, 2m, 1y). '
            'Appended to the effective query. Overrides [search] lookback '
            'in config.toml'
        ),
    )
    run_p.add_argument(
        '--dry-run',
        action='store_true',
        help='classify only; do not reply or archive',
    )
    del_group = run_p.add_mutually_exclusive_group()
    del_group.add_argument(
        '--delete-only',
        action='store_true',
        help=(
            'for any message classified COLD_SALES or BULK_MARKETING, '
            'trash the thread instead of replying / unsubscribing'
        ),
    )
    del_group.add_argument(
        '--delete',
        action='store_true',
        help=(
            'reply to COLD_SALES as usual, then trash the thread '
            'instead of archiving it'
        ),
    )
    run_p.add_argument(
        '--daemon',
        action='store_true',
        help='run continuously; re-process the inbox every --interval',
    )
    run_p.add_argument(
        '--interval',
        type=_parse_interval,
        default=DEFAULT_INTERVAL_SECONDS,
        help='daemon interval (e.g. 30s, 15m, 2h); default 15m',
    )
    out = run_p.add_mutually_exclusive_group()
    out.add_argument(
        '--plain',
        dest='output',
        action='store_const',
        const=reporter.Mode.PLAIN,
        help='disable ANSI colours and render a pipe-delimited table',
    )
    out.add_argument(
        '--json',
        dest='output',
        action='store_const',
        const=reporter.Mode.JSON,
        help='emit only the parsed summary as JSON on stdout',
    )
    run_p.set_defaults(output=reporter.Mode.PRETTY)

    report_p = sub.add_parser(
        'report',
        help='render (or email) an HTML report of recent actions',
    )
    report_p.add_argument(
        '--days',
        type=int,
        default=7,
        help='last N calendar days, today inclusive; default 7',
    )
    report_p.add_argument(
        '--since',
        type=datetime.date.fromisoformat,
        default=None,
        help='start date (YYYY-MM-DD); overrides --days',
    )
    report_p.add_argument(
        '--to',
        action='append',
        default=None,
        metavar='ADDR',
        help=(
            'recipient for --email (repeatable); overrides '
            '[report] recipients in config.toml'
        ),
    )
    report_p.add_argument(
        '--subject',
        default=None,
        help='override the emailed report subject line',
    )
    report_p.add_argument(
        '--email',
        action='store_true',
        help='email the report instead of writing HTML to stdout/--output',
    )
    report_p.add_argument(
        '--output',
        type=pathlib.Path,
        default=None,
        help='write the HTML report to this file instead of stdout',
    )
    return parser


def _configure_logging(*, verbose: bool, logfile: pathlib.Path | None) -> None:
    # Log to stderr so --json stdout stays parseable.
    level = logging.DEBUG if verbose else logging.INFO
    fmt = '%(asctime)s %(levelname)s %(name)s: %(message)s'
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding='utf-8'))
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=handlers,
        force=True,
    )
    # SDK's own INFO stream is too chatty for our usage.
    logging.getLogger('claude_agent_sdk').setLevel(logging.WARNING)


def _cmd_auth() -> int:
    creds_file = config.credentials_path()
    token_file = config.token_path()
    auth.load_credentials(
        credentials_file=creds_file,
        token_file=token_file,
        scopes=config.GMAIL_SCOPES,
    )
    print(f'Gmail credentials stored at {token_file}')  # noqa: T201
    return 0


async def _run_once(
    settings: settings_mod.Settings,
    args: argparse.Namespace,
) -> int:
    rpt = reporter.Reporter(mode=args.output)
    return await agent.run(
        settings=settings,
        reporter=rpt,
        query=args.query,
        max_results=args.max_results,
        lookback=args.lookback,
        dry_run=args.dry_run,
        delete_only=args.delete_only,
        delete=args.delete,
    )


async def _run_daemon(
    settings: settings_mod.Settings,
    args: argparse.Namespace,
) -> int:
    interval = args.interval
    LOGGER.info('daemon mode: every %ds (Ctrl-C to stop)', interval)
    while True:
        try:
            rc = await _run_once(settings, args)
            LOGGER.debug('iteration finished (rc=%d)', rc)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception('iteration failed, continuing')
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def _cmd_report(args: argparse.Namespace) -> int:
    settings = settings_mod.Settings.load()
    html = report.render(settings.db_path, days=args.days, since=args.since)
    if args.email:
        recipients = tuple(args.to) if args.to else settings.report_recipients
        if not recipients:
            LOGGER.error(
                'no recipients; set [report] recipients in config.toml '
                'or pass --to'
            )
            return 2
        creds = auth.load_credentials(
            credentials_file=config.credentials_path(),
            token_file=config.token_path(),
            scopes=config.GMAIL_SCOPES,
        )
        client = gmail.GmailClient(creds)
        client.send_html(
            to=recipients,
            subject=args.subject or settings.report_subject,
            html=html,
        )
        LOGGER.info('report emailed to %s', ', '.join(recipients))
        return 0
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html, encoding='utf-8')
        LOGGER.info('report written to %s', args.output)
        return 0
    sys.stdout.write(html)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    settings = settings_mod.Settings.load()
    coro = (
        _run_daemon(settings, args)
        if args.daemon
        else _run_once(settings, args)
    )
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        LOGGER.info('interrupted; shutting down')
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(verbose=args.verbose, logfile=args.logfile)
    match args.command:
        case 'auth':
            return _cmd_auth()
        case 'run':
            return _cmd_run(args)
        case 'report':
            return _cmd_report(args)
        case _:  # pragma: no cover - argparse enforces choices
            parser.error(f'unknown command: {args.command}')
            return 2


if __name__ == '__main__':
    sys.exit(main())
