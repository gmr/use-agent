# use-agent

A Claude Agent that scans a Gmail inbox for unsolicited sales email,
replies in the user's voice, and archives the thread.

Built on the [Claude Agent SDK][sdk]. Gmail API operations are
exposed to the agent as tools via an in-process MCP server. All
user-specific configuration lives in a single `config.toml` — the
repo itself contains no identifying information.

[sdk]: https://docs.claude.com/en/api/agent-sdk/overview

## What it does

1. Searches the inbox for unread messages from outside senders (the
   query is built from the safelist in `config.toml`).
2. Fetches each candidate and checks whether the thread already has
   a sent reply.
3. Classifies each message using the rules in
   [`use_agent/prompts/classifier.md`](use_agent/prompts/classifier.md).
4. For every `COLD_SALES` hit, generates a reply using the rules in
   [`use_agent/prompts/reply.md`](use_agent/prompts/reply.md).
5. Sends the reply as a proper threaded reply (`In-Reply-To` and
   `References` headers set from the original message, same
   `threadId` passed to the send call), marks the original as read,
   and archives the thread.
6. Emits a summary of everything examined.

Unlike the Claude Cowork Gmail connector, this agent sends actual
replies rather than drafts and can archive threads.

## Install

Requires Python 3.14 and [`uv`][uv].

```bash
uv sync
```

This installs the runtime deps (`claude-agent-sdk`,
`google-api-python-client`, `google-auth-oauthlib`, `jinja2`,
`rich`) and the dev tools (`pytest`, `ruff`, `coverage`). The
console script `use-agent` lands at `.venv/bin/use-agent`.

[uv]: https://docs.astral.sh/uv/

## Configure

Copy `config.example.toml` to `~/.config/use-agent/config.toml` (or
point `USE_AGENT_CONFIG` at any path) and fill in the sections
below. The real `config.toml` is gitignored; only the example is
committed.

```toml
[user]
name = "Your Name"
organization = "Your Company"

[safelist]
# Never classified as cold sales, and auto-appended as -from:<d>
# filters to the Gmail search query.
domains = ["example.com", "example.net"]

[vendors]
# Exempt from cold-sales classification when the message is about
# billing / renewal / account management. Vendor reps pitching
# upsells still get flagged.
names = ["AWS", "GitHub"]

[voice]
# Rendered as a bulleted list under "## Voice Guidelines" in the
# reply prompt.
guidelines = [
    "1-2 sentences maximum, never more",
    "No apology for declining",
    "Blunt but not hostile",
    "Always ends with a remove request (except `specific_decline`)",
]
# Footer appended to every reply. May reference Jinja variables.
# Set to "" to disable.
footer = """\
---
This email was flagged as unsolicited sales outreach and this reply
was sent on {{ user_name }}'s behalf by an automated assistant.\
"""

[agent]
# Claude model that drives the agent.
model = "claude-haiku-4-5-20251001"

[search]
max_results = 25
# Override the Gmail query. If omitted, the query is built from
# "in:inbox is:unread" plus a -from:<domain> filter for every
# safelist entry.
# query = "in:inbox is:unread"
```

Both `classifier.md` and `reply.md` are Jinja2 templates; their
contents are rendered at startup using the values above. Editing
them changes agent behavior without any Python change.

### Environment variable overrides

| Variable                | Purpose                          | Default                                |
| ----------------------- | -------------------------------- | -------------------------------------- |
| `USE_AGENT_CONFIG`      | Path to `config.toml`            | `~/.config/use-agent/config.toml`      |
| `USE_AGENT_CREDENTIALS` | Path to OAuth client secret      | `~/.config/use-agent/credentials.json` |
| `USE_AGENT_TOKEN`       | Path to stored OAuth token       | `~/.config/use-agent/token.json`       |
| `XDG_CONFIG_HOME`       | Base for the above defaults      | `~/.config`                            |
| `ANTHROPIC_API_KEY`     | Required by the Claude Agent SDK | —                                      |

## Gmail OAuth setup

1. In Google Cloud Console, pick a project that has the Gmail API
   enabled and create an **OAuth 2.0 Client ID** of type _Desktop
   app_.
2. Download the client secret as `credentials.json`. Drop it at
   `~/.config/use-agent/credentials.json` (or point
   `USE_AGENT_CREDENTIALS` at wherever you saved it).
3. Run the one-time authorization flow:

   ```bash
   uv run use-agent auth
   ```

   This opens a browser, completes the OAuth consent, and stores
   the refresh token at `~/.config/use-agent/token.json` (mode
   `0600`). Refreshes happen automatically on subsequent runs.

The only scope requested is
`https://www.googleapis.com/auth/gmail.modify`, which covers read,
label changes (archive, mark read), and send — nothing destructive.

## Run

```bash
# One-shot
uv run use-agent run                    # process the inbox
uv run use-agent run --dry-run          # classify but don't reply/archive
uv run use-agent run --max 10           # examine at most 10 candidates
uv run use-agent run --query 'is:unread label:followup'  # custom query

# Output format (default is pretty)
uv run use-agent run --plain            # no ANSI, pipe-delimited table
uv run use-agent run --json             # stdout is a single JSON document

# Daemon mode
uv run use-agent run --daemon                   # loop forever, every 15m
uv run use-agent run --daemon --interval 30m    # 30-minute cadence
uv run use-agent run --daemon --interval 2h     # every two hours
# Intervals accept s / m / h / d suffixes, or raw seconds.

# Logging
uv run use-agent -v run                         # DEBUG level logs to stderr
uv run use-agent --logfile run.log run --daemon # tee logs to a file
```

### Output modes

- **`--pretty` (default):** Rich table on stdout, colour-coded
  classification, auto-wrapping columns. Running commentary ("Now
  fetching message…", "Classifying…") streams on stderr via the
  `use_agent.narration` logger.
- **`--plain`:** No ANSI. A pipe-delimited ASCII table hits stdout —
  safe to pipe into `column`, `less`, `tee`, etc.
- **`--json`:** A single JSON document on stdout:
  `{"results": [{...}, ...]}`. All logs and narration go to stderr,
  so `use-agent run --json | jq '.results[].classification'` is
  clean.

Exit code is `0` on a successful run with a parsed summary, `1` if
the agent produced no recognizable summary block.

### Daemon mode

`--daemon` loops until Ctrl-C. Each iteration spins up a fresh
reporter and agent run; exceptions inside an iteration are logged
and the loop continues. Pair with `--logfile` for long-running
deployments:

```bash
use-agent --logfile ~/logs/use-agent.log run --daemon --interval 30m
```

### Logging

The CLI emits structured logs to stderr (and, with `--logfile`, to
the named file too). Loggers:

| Logger                | Level   | Content                                                 |
| --------------------- | ------- | ------------------------------------------------------- |
| `use_agent.*`         | INFO    | High-level lifecycle (agent run start/end, daemon tick) |
| `use_agent.narration` | INFO    | Running commentary from the agent                       |
| `use_agent.tools`     | DEBUG   | Every Gmail tool call (search, get, reply, archive)     |
| `claude_agent_sdk`    | WARNING | Pinned to WARN; SDK's INFO is too chatty                |

`-v` / `--verbose` bumps the root level to DEBUG, surfacing every
tool invocation.

## Isolation from your Claude Code setup

The agent is deliberately hermetic. `ClaudeAgentOptions` is
constructed with:

- `setting_sources=[]` — don't merge in `~/.claude/settings.json`,
  any `.claude/settings.local.json`, or project-level settings
- `settings=None` — no specific settings file
- `skills=None` — no auto-discovered skills
- `mcp_servers={'gmail': <our server>}` — only our Gmail MCP server
- `allowed_tools=[...]` — only the four Gmail tools we expose

Nothing from your Claude Code dev environment — custom agents,
plugins, MCP servers, hooks, skills — can leak into this agent run.

## How the agent decides

Classification is rule-based, not sentiment-based. Each message
accumulates points from STRONG (2pt) and WEAK (1pt) signals — meeting
CTA, intro formulas, outreach-tooling domains, flattery hooks, fake
`Re:` threads, false premises about the org, etc. A total score of
`>= 3` means `COLD_SALES`. Vendor billing, newsletters, transactional
notifications, threads that already have a sent reply, and senders
from safelisted domains are hard-exempt regardless of score.

Replies are one of four modes:

- `hard_remove` — the default. A curt "Not interested, please remove."
- `hard_remove_with_correction` — when the sender got a fact about
  the org wrong; lead with a brief factual correction, then remove.
- `specific_decline` — reserved for reps of current vendors.
- `none` — not cold sales; skip.

Every reply optionally ends with the `footer` from `config.toml`,
which is itself a Jinja string (so it can interpolate `{{
user_name }}` etc.).

## How replies are threaded

Gmail treats a reply as part of the original thread only when the
send request is correctly chained. `gmail.py`:

1. Pulls the original `Message-ID` and `References` headers.
2. Builds a new `EmailMessage` with `In-Reply-To: <original-id>` and
   `References: <chain> <original-id>`.
3. Subject is prefixed with `Re: ` unless it already starts with
   `Re:`.
4. Base64url-encodes the MIME message and calls
   `users.messages.send` with the encoded body **and** the original
   `threadId`.

That combination is what Gmail's UI uses to collapse the reply into
the same conversation view the original arrived in.

## License

BSD-3-Clause.
