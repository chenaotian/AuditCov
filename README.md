# AuditCov

AuditCov is a small Codex-first prototype for tracking objective read coverage during AI-assisted code audits.

The v0 MCP server intentionally tracks only one thing:

> Which complete source-code lines were returned to the model through the AuditCov read tool.

It does not claim the model understood those lines, and it does not claim a high percentage means the audit is complete. Low coverage is evidence that the audit did not inspect enough target code; high coverage only means the model was exposed to more target code.

## v0 Scope

Implemented MCP tools:

- `auditcov_init_project`
- `auditcov_read_file`
- `auditcov_get_coverage`
- `auditcov_get_file_detail`
- web coverage viewer

Deferred from v0:

- search coverage
- file discovery tracking
- subjective coverage reporting
- rollout-based coverage reconstruction

Rollout bypass detection is implemented as best-effort internal logging, not as a model-facing MCP tool.

## Run

```powershell
python -m auditcov_mcp.server
```

The server stores state in `.auditcov/auditcov.sqlite3` under the process working directory by default. Set `AUDITCOV_DB` to use another database path.

## Web Viewer

Run the coverage UI:

```powershell
python -m auditcov_mcp.web --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

The viewer reads the same SQLite database as the MCP server. It shows:

- all initialized Codex thread projects
- project-level objective read coverage
- target directory and file coverage
- per-file covered and uncovered source lines

Set `AUDITCOV_DB` for both the MCP server and web viewer when they should share a database outside the default `.auditcov/auditcov.sqlite3` path.

## Task Identity

AuditCov v0 is Codex-first. It identifies a task from Codex MCP metadata:

```text
params._meta["x-codex-turn-metadata"].thread_id
```

The server also records `turn_id` on read events when present. Tool arguments do not accept `project_id`, `session_id`, or `thread_id`, so the model cannot choose or spoof coverage scopes through normal tool inputs.

## Rollout Bypass Logging

Set `AUDITCOV_ROLLOUT_DIR` to a directory containing Codex rollout `.json` or `.jsonl` files to enable best-effort bypass detection:

```powershell
$env:AUDITCOV_ROLLOUT_DIR = "D:/path/to/codex/jsonl"
python -m auditcov_mcp.server
```

When AuditCov sees common direct-read shell commands such as `cat`, `sed`, `Get-Content`, `rg`, or `grep` in rollout records for the same `thread_id` or `session_id`, it writes a stderr log line:

```text
[AUDITCOV_BYPASS] thread_id=... kind=possible_direct_file_read source=... command="..."
```

These warnings do not change coverage. They only tell a human reviewer that the MCP-only coverage number may be incomplete.

## MCP Tools

### auditcov_init_project

Freeze the denominator for the current Codex thread.

Arguments:

```json
{
  "project_root": "D:/repo/example",
  "target_paths": ["src", "include"]
}
```

Behavior:

- `project_root` must exist.
- every target path must stay under `project_root`.
- only built-in source-code extensions are included.
- symlinked files and directories are skipped.
- generated/vendor-style directories such as `.git`, `node_modules`, `dist`, `build`, and `target` are skipped by fixed policy.
- repeated initialization with the same frozen snapshot is idempotent.
- repeated initialization with different inputs or changed file content returns an error.

### auditcov_read_file

Read a target file and record returned complete lines as objective read coverage.

Arguments:

```json
{
  "path": "src/a.c",
  "start_line": 1,
  "end_line": 200
}
```

`start_line` defaults to `1`. `end_line` may be omitted to read as much as possible from `start_line`, bounded by the fixed 40KB response limit.

If the response would exceed 40KB, AuditCov truncates at the last complete returned line and includes `next_start_line`.

### auditcov_get_coverage

Return project, directory, or file objective read coverage.

Arguments:

```json
{
  "path": null
}
```

`path` omitted or `null` means the whole project snapshot. A directory path returns aggregate directory coverage. A file path returns file coverage.

### auditcov_get_file_detail

Return exact covered and uncovered ranges for one file.

Arguments:

```json
{
  "path": "src/a.c"
}
```

Ranges are 1-based and inclusive.

## Development

Run tests:

```powershell
python -m unittest discover -s tests
```
