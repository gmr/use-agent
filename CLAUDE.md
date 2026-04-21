# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`use-agent` is a Claude Agent that triages a Gmail inbox for
unsolicited sales email, replies in the user's voice, and archives
the thread. It's built on the Claude Agent SDK; Gmail API operations
are exposed as tools via an in-process SDK MCP server.

Python 3.14, uv, hatchling + hatch-vcs, ruff. Runtime deps:
`claude-agent-sdk`, `google-api-python-client`,
`google-auth-oauthlib`, `jinja2`, `rich`.

## Commands

```bash
uv sync                               # install runtime + dev deps
uv run use-agent auth                 # one-time Gmail OAuth
uv run use-agent run --dry-run        # classify only
uv run use-agent run                  # full run: reply + archive
uv run use-agent run --daemon --interval 30m --logfile run.log
uv run use-agent run --json | jq .    # stdout is pure JSON

uv run ruff check .
uv run ruff format --check .
uv run coverage run                   # pytest under coverage
uv run coverage report
```

`ANTHROPIC_API_KEY` is required for any `run`.

## Architecture

The data flow is deliberately narrow. All user-specific context enters
through `config.toml`; everything else is derivable from the code.

```
cli.main → Settings.load() → agent.run(settings, reporter)
                                 ↓
                     _render_system_prompt(settings)
                                 ↓
                     claude_agent_sdk.query(
                         prompt=<user-prompt>,
                         options=ClaudeAgentOptions(
                             system_prompt=<rendered>,
                             mcp_servers={'gmail': <built from GmailClient>},
                             allowed_tools=[mcp__gmail__search, ...],
                             setting_sources=[],  # hermetic
                             model=<from config>,
                         ),
                     )
                                 ↓
                      streaming AssistantMessage/TextBlock
                                 ↓
                        reporter.on_text(...)   (→ narration logger)
                                 ↓
                        reporter.finish()       (→ stdout: table/JSON)
```

Key module responsibilities:

- `gmail.GmailClient` is the only module that touches the Gmail REST
  API. It returns frozen `Message` dataclasses so tool output stays
  JSON-friendly.
- `tools.build_mcp_server(client)` wraps four `@claude_agent_sdk.tool`
  closures (`search`, `get_message`, `reply`, `archive_and_mark_read`)
  over a `GmailClient` and returns an SDK MCP server. The closure
  approach lets the tools share state without a module-global client.
- `agent.run()` is the orchestration entry point: renders the system
  prompt, builds the tool server, configures `ClaudeAgentOptions`,
  streams responses into the `Reporter`, and returns an exit code.
  Python does no per-message logic — the agent drives the loop.
- `reporter.Reporter` owns all output. **`on_text` never writes to
  stdout**; narration goes to the `use_agent.narration` logger
  instead. `finish()` parses the last ` ```json ` fenced block from
  the buffered text and renders it as a Rich table (`pretty`),
  pipe-delimited ASCII (`plain`), or a JSON document (`json`).
- `settings.Settings` is a frozen dataclass loaded from
  `config.toml` (path via `USE_AGENT_CONFIG` env var or
  `~/.config/use-agent/config.toml`). If `[search] query` is omitted,
  a default `in:inbox is:unread` query is built from the safelist
  domains as `-from:<domain>` filters.

### Prompts are Jinja2 templates

`use_agent/prompts/classifier.md` and `reply.md` are Jinja2 templates
rendered at startup. Context injected by `agent._render_context`:
`user_name`, `organization`, `safelist_domains`, `vendor_names`,
`voice_guidelines`, `reply_footer`, plus two pre-rendered strings
(`voice_block`, `footer_block`, `footer_instruction`). The
pre-rendered strings exist because the repo's pre-commit Markdown
formatter strips blank lines inside `{% if %}` blocks — avoid Jinja
control flow in prompt Markdown; pre-render composite blocks in
Python and inject them as single `{{ ... }}` substitutions.

`reply_footer` is itself a Jinja string (may reference `{{ user_name
}}` etc.) and is rendered once in `_render_context` before being
injected into the reply template.

### Editing agent behavior without touching Python

Most behavior tweaks belong in `config.toml` or the prompt Markdown:

- New classification signal → edit `prompts/classifier.md`
- Change reply voice / add a template → edit `prompts/reply.md`
- Add a vendor exemption → `[vendors] names` in `config.toml`
- Add a safelisted domain → `[safelist] domains` in `config.toml`
- Change footer text / disable footer → `[voice] footer` (or `""`)
- Change model → `[agent] model`

Python changes are only needed when the set of tools, output modes,
CLI flags, or Gmail operations needs to change.

### Hermetic SDK configuration

`agent.run()` passes `setting_sources=[]`, `settings=None`,
`skills=None` to `ClaudeAgentOptions` so the agent never merges in
the developer's `~/.claude/settings.json`, project-level Claude Code
config, plugins, or auto-discovered skills. The only MCP server the
agent sees is our Gmail one; the only tools it can call are the four
in `tools.ALLOWED_TOOLS`. Keep it that way — preserving isolation is
part of the trust model.

### Threaded replies

Gmail only collapses a reply into the original conversation when all
three are correct: `In-Reply-To: <original Message-ID>`,
`References: <chain> <original Message-ID>`, and `threadId` in the
`users.messages.send` request. `gmail.GmailClient.reply` assembles
all three from the fetched original. Don't regress this — dropping
`threadId` or the headers silently demotes replies into new threads.

### Output contract

The agent is prompted to emit exactly one fenced ` ```json ` block
containing `{"results": [...]}`. The reporter's `_extract_summary`
walks the buffered text from the end backwards looking for the last
parseable JSON fence with a `results` array (or a bare list of rows
with a `classification` key). If the agent forgets to emit the
block, `finish()` returns exit code 1 — treat this as a real error.

### Logging

| Logger | Level | Purpose |
|---|---|---|
| `use_agent.*` | INFO | Notable one-offs (OAuth flow, reply sent); lifecycle (run start, daemon tick, cache stats) is DEBUG |
| `use_agent.narration` | INFO | Agent's running commentary (buffered text) |
| `use_agent.tools` | DEBUG | Per-tool-call Gmail operations |
| `claude_agent_sdk` | WARNING | Pinned; SDK INFO is too chatty |

All handlers write to stderr (plus the `--logfile` target when set)
so stdout stays pure in `--json` mode.

## Conventions

- **Module-level imports only.** A pre-commit hook rejects `from
  pathlib import Path` / `from typing import Any` style imports —
  use `import pathlib` / `import typing`, then `pathlib.Path(...)`,
  `typing.Any`.
- Ruff: 79-char lines, single quotes, `py314` target. Config lives
  in `pyproject.toml`.
- No compound shell commands: each `Bash` call is one command
  (repo-wide preference, not project-specific, but still applies).
- `credentials.json`, `token.json`, and `config.toml` are all
  gitignored. The repo itself contains no identifying information;
  `config.example.toml` uses placeholder values only.

## Version

`use_agent.__version__` is read at runtime via
`importlib.metadata.version('use-agent')`, with a `0.0.0+unknown`
fallback. `[project] version` is pinned in `pyproject.toml`; hatch-vcs
is configured but not currently generating a version file.
