# AuditCov

AuditCov is a local objective source-read coverage tracker for Codex, Claude Code, and OpenCode.

It records which complete source lines were successfully returned by each agent's file-reading path. It does not claim the agent understood those lines and does not treat high read coverage as proof that a code audit is complete.

## Architecture

```text
Codex ------ MCP adapter -------+
Claude Code Read hooks ---------+--> AuditCov HTTP server --> SQLite
OpenCode Read hooks ------------+             |
                                               +--> Web UI
```

The central server owns all project snapshots, sessions, read events, covered ranges, coverage queries, and Web assets. Agent adapters never maintain their own coverage database.

## Run the server

```powershell
python -m auditcov_mcp.web
```

Open `http://127.0.0.1:8765`, then create a project by entering its repository root. Project creation freezes the first-version denominator for the whole repository using the built-in source extensions and exclusion policy.

The server listens on localhost by default. Set `AUDITCOV_SERVER_URL` in an agent environment if the adapters should use a different local URL. State defaults to `.auditcov/auditcov.sqlite3`; `AUDITCOV_DB` and `AUDITCOV_WORK_DIR` remain available as overrides.

## Install agent adapters

The dependency-free installer supports Windows and Linux:

```powershell
python scripts/auditcov_install.py install --codex
python scripts/auditcov_install.py install --claude --opencode
python scripts/auditcov_install.py install --all
python scripts/auditcov_install.py status
```

Uninstall any combination with the same selectors:

```powershell
python scripts/auditcov_install.py uninstall --claude --opencode
python scripts/auditcov_install.py uninstall --all
```

Restart the selected agents after installing or uninstalling adapters. Start a new Codex task after installing or refreshing the Codex plugin.

### Codex

The Codex plugin contains a skill and a thin stdio MCP adapter. It exposes:

- `auditcov_read_file`
- `auditcov_get_coverage`
- `auditcov_get_file_detail`

There is no MCP initialization tool. Projects are created in the Web UI. `auditcov_read_file` asks the central server to validate, read, return, and count complete source lines. The two query tools proxy the current `thread_id` to the server.

### Claude Code

The installer adds global `PreToolUse(Read)` and `PostToolUse(Read)` handlers without replacing unrelated hooks.

- Before execution, the adapter reports the attempted path/range. If the file belongs to no configured project, it returns no output and changes nothing.
- For a tracked file, the server can reduce `limit` so the complete-line payload stays within 256 KiB.
- `PostToolUse` runs only after a successful Read and confirms the actual range to the server. Only this phase changes coverage.
- A server outage never blocks Read. It appends a warning to the platform state directory's `auditcov/hook-warnings.log`.

### OpenCode

The installer copies a global OpenCode TypeScript plugin under the user's OpenCode plugin directory.

- `tool.execute.before(read)` reports the attempt and mutates `output.args` only for a tracked project file.
- Tracked reads are reduced at complete-line boundaries to stay within 51,200 bytes.
- `tool.execute.after(read)` reports the result and successful returned range.
- A server outage leaves Read unchanged and writes the same local warning log.

## Project and coverage rules

- A Web project covers one complete repository; v1 has no target-subdirectory selector.
- Project roots may not be equal, ancestors, or descendants of one another.
- Only built-in source-code extensions are snapshotted.
- Symlinked files/directories and fixed generated/vendor directories are skipped.
- Reads outside configured projects, and reads of files outside a frozen source snapshot, are ignored.
- The denominator is shared by all sessions under the project.
- A session is identified by `codex/thread_id`, `claude-code/session_id`, or `opencode/sessionID`.
- Before events are attempts only. Coverage changes only after confirmed success.
- Selecting multiple sessions in the Web UI uses the union of their covered line ranges against the one project snapshot.

## HTTP API

Project and Web queries:

- `POST /api/projects`
- `GET /api/projects`
- `GET /api/projects/{id}`
- `GET /api/projects/{id}/coverage`
- `GET /api/projects/{id}/file`

Agent ingestion and Codex proxy endpoints:

- `POST /api/read/before`
- `POST /api/read/after`
- `POST /api/codex/read`
- `GET /api/agent/coverage`
- `GET /api/agent/file-detail`

The service is intentionally local and unauthenticated in v1. Do not expose it on a network interface.

## Development

```powershell
python -m unittest discover -s tests -v
python C:\Users\Administrator\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py plugins\auditcov
```

The older MCP-parameter and Read-hook probes remain under `tools/`, `hooks/read_probe/`, `scripts/`, and `docs/` as diagnostic utilities; they are not the production adapters.
