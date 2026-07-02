"""Render agent output in pretty, plain, or JSON mode.

- ``pretty`` (default): stream assistant text as Markdown; render the
  final summary as a Rich table on stdout.
- ``plain``: stream raw text; render the summary as a pipe-delimited
  ASCII table. No ANSI.
- ``json``: suppress intermediate text on stdout; print only the
  parsed summary as a single JSON document. Progress logging still
  goes to stderr.
"""

import dataclasses
import enum
import json
import logging
import re
import sys
import typing

import rich.console
import rich.json
import rich.markdown
import rich.table

LOGGER = logging.getLogger(__name__)
NARRATION_LOGGER = logging.getLogger('use_agent.narration')

_SUMMARY_FENCE = re.compile(
    r'```json\s*\n(?P<body>.*?)\n```',
    re.DOTALL,
)

_COLUMNS = (
    'sender',
    'subject',
    'classification',
    'score',
    'response_mode',
    'action_taken',
)


class Mode(enum.StrEnum):
    PRETTY = 'pretty'
    PLAIN = 'plain'
    JSON = 'json'


@dataclasses.dataclass(slots=True)
class Reporter:
    """Collect streamed assistant text and emit a final summary."""

    mode: Mode = Mode.PRETTY
    summary: list[dict[str, typing.Any]] | None = None
    _buffer: list[str] = dataclasses.field(default_factory=list)
    _stdout: rich.console.Console = dataclasses.field(init=False)
    _stderr: rich.console.Console = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self._stdout = rich.console.Console(
            file=sys.stdout,
            force_terminal=self.mode is Mode.PRETTY,
            no_color=self.mode is not Mode.PRETTY,
            highlight=self.mode is Mode.PRETTY,
        )
        self._stderr = rich.console.Console(
            file=sys.stderr,
            force_terminal=self.mode is Mode.PRETTY,
            no_color=self.mode is not Mode.PRETTY,
        )

    def on_text(self, text: str) -> None:
        """Record streamed assistant text.

        The text is buffered for later summary extraction and sent to
        the ``use_agent.narration`` logger. It is NOT written to
        stdout — stdout is reserved for the final summary so ``--json``
        output stays parseable and ``--pretty`` stays uncluttered.
        Attach a stream or file handler to ``use_agent.narration`` if
        you want to see the running commentary live.

        Chunks that contain a ```` ```json ```` fence — the agent's
        summary block plus its filler preamble ("Here are the
        results:") — are logged at DEBUG so they only surface under
        ``-v``. The reporter renders the summary as a table, so the
        prose alongside it is duplication.
        """
        self._buffer.append(text)
        stripped = text.strip()
        if not stripped:
            return
        if _SUMMARY_FENCE.search(text):
            NARRATION_LOGGER.debug('%s', stripped)
        else:
            NARRATION_LOGGER.info('%s', stripped)

    def finish(self) -> int:
        """Render the final summary. Return process exit code."""
        full = '\n'.join(self._buffer)
        results = _extract_summary(full)
        self.summary = results
        match self.mode:
            case Mode.JSON:
                payload = {'results': results or []}
                json.dump(payload, sys.stdout, indent=2, default=str)
                sys.stdout.write('\n')
            case Mode.PRETTY:
                self._render_pretty_table(results)
            case Mode.PLAIN:
                self._render_plain_table(results)
        return 0 if results is not None else 1

    def log(self, message: str) -> None:
        """Emit a progress message to stderr (never stdout)."""
        self._stderr.print(message)

    def _render_pretty_table(
        self, results: list[dict[str, typing.Any]] | None
    ) -> None:
        if results is None:
            LOGGER.error('agent produced no summary')
            return
        if not results:
            LOGGER.info('No new unread emails to process')
            return
        table = rich.table.Table(
            header_style='bold',
            show_lines=False,
        )
        table.add_column('Sender', overflow='fold', max_width=32)
        table.add_column('Subject', overflow='fold', max_width=44)
        table.add_column('Classification')
        table.add_column('Score', justify='right')
        table.add_column('Mode')
        table.add_column('Action', overflow='fold', max_width=36)
        for row in results:
            classification = str(row.get('classification', ''))
            style = 'red' if classification == 'COLD_SALES' else 'green'
            table.add_row(
                str(row.get('sender', '')),
                str(row.get('subject', '')),
                f'[{style}]{classification}[/{style}]',
                str(row.get('score', '')),
                str(row.get('response_mode', '')),
                str(row.get('action_taken', '')),
            )
        self._stdout.print(table)

    def _render_plain_table(
        self, results: list[dict[str, typing.Any]] | None
    ) -> None:
        if results is None:
            LOGGER.error('agent produced no summary')
            return
        if not results:
            LOGGER.info('No new unread emails to process')
            return
        rows = [[str(r.get(c, '')) for c in _COLUMNS] for r in results]
        widths = [
            max(len(c.upper()), *(len(r[i]) for r in rows))
            for i, c in enumerate(_COLUMNS)
        ]
        header = ' | '.join(
            c.upper().ljust(widths[i]) for i, c in enumerate(_COLUMNS)
        )
        divider = '-+-'.join('-' * w for w in widths)
        print(header)  # noqa: T201
        print(divider)  # noqa: T201
        for row in rows:
            print(  # noqa: T201
                ' | '.join(cell.ljust(widths[i]) for i, cell in enumerate(row))
            )


def _extract_summary(
    text: str,
) -> list[dict[str, typing.Any]] | None:
    """Return the last ```json fenced block parsed as a result list."""
    matches = list(_SUMMARY_FENCE.finditer(text))
    for match in reversed(matches):
        body = match.group('body').strip()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        results = _coerce_results(parsed)
        if results is not None:
            return results
    return None


def _coerce_results(
    parsed: typing.Any,
) -> list[dict[str, typing.Any]] | None:
    if isinstance(parsed, dict) and isinstance(parsed.get('results'), list):
        return [r for r in parsed['results'] if isinstance(r, dict)]
    if isinstance(parsed, list) and all(isinstance(r, dict) for r in parsed):
        # Accept a bare list if every entry looks like a row.
        if parsed and all('classification' in r for r in parsed):
            return parsed
    return None
