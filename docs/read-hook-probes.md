# Read Hook Probes for Claude Code and OpenCode

These hooks record both the attempted parameters and the post-execution result of the built-in file-reading tool:

- Claude Code: `PreToolUse(Read)` and `PostToolUse(Read)`.
- OpenCode: `tool.execute.before(read)` and `tool.execute.after(read)`.

Both append normalized events to `~/.local/state/auditcov-read-hook-probe/events.jsonl`. The hooks observe only the built-in Read/read tool. They do not record direct reads performed through Bash, PowerShell, Grep, LSP, or another MCP server.

`after` events include the complete tool result and can therefore contain source-file contents. The log is created with user-only permissions, but it should still be treated as sensitive audit data. Claude's `PostToolUse` is success-specific and uses `outcome: "succeeded"`; OpenCode's generic `tool.execute.after` uses the conservative `outcome: "completed"`, so consumers must inspect its result before counting coverage.

## Install in WSL

Run from this repository inside WSL:

```bash
python3 scripts/install_read_hook_probes_wsl.py install
python3 scripts/install_read_hook_probes_wsl.py status
```

The installer adds managed `PreToolUse(Read)` and `PostToolUse(Read)` handlers to `~/.claude/settings.json`, copies their shared Python recorder under `~/.local/share/auditcov-read-hook-probe/`, and installs one OpenCode TS plugin under `~/.config/opencode/plugins/`. Existing unrelated Claude hooks and OpenCode configuration are preserved.

The Claude handler stores the complete Python invocation in the `command` field. This shell-form configuration is intentional: Claude Code 2.1.138 can display an `args`-based handler in `/hooks` while launching only the bare `python3` executable.

Restart Claude Code and OpenCode after installation.

## Prepare a test path

The installed Claude hook itself is a convenient text file. Resolve its absolute path outside the clients:

```bash
realpath ~/.local/share/auditcov-read-hook-probe/claude_read_hook.py
```

Use the resulting absolute path as `ABS_FILE` in the prompts below. Also choose an existing absolute directory as `ABS_DIR`. The prompts deliberately constrain tool choice so a Bash or Grep read does not invalidate the test.

## Claude Code test prompts

Whole-file form, with both optional fields omitted:

```text
只调用一次 Claude Code 内置的 Read 工具，不要使用 Bash、Grep、Glob、Agent 或任何 MCP 工具。Read 参数必须只包含 file_path="ABS_FILE"，明确省略 offset 和 limit。工具返回后立即停止，不要总结文件内容。
```

Explicit range:

```text
只调用一次内置 Read 工具，不要使用其他工具。参数必须精确为 file_path="ABS_FILE", offset=5, limit=7。调用后立即停止。
```

Offset only:

```text
只调用一次内置 Read 工具。参数必须包含 file_path="ABS_FILE" 和 offset=10，并且必须省略 limit。不要使用其他工具，调用后停止。
```

Limit only:

```text
只调用一次内置 Read 工具。参数必须包含 file_path="ABS_FILE" 和 limit=6，并且必须省略 offset。不要使用其他工具，调用后停止。
```

Failure path, which still fires `PreToolUse`:

```text
只调用一次内置 Read 工具，参数只使用 file_path="/tmp/auditcov-read-probe-file-that-does-not-exist"。即使你知道文件不存在也必须调用；不要改用其他工具。调用后停止。
```

If an existing PDF is available, exercise the PDF-specific field:

```text
只调用一次内置 Read 工具读取 PDF。参数精确使用 file_path="ABS_PDF" 和 pages="1-2"，省略 offset 和 limit。不要使用其他工具，调用后停止。
```

## OpenCode test prompts

Whole-file form:

```text
只调用一次 OpenCode 内置的 read 工具，不要使用 bash、grep、glob、lsp、task 或任何 MCP 工具。read 参数必须只包含 filePath="ABS_FILE"，明确省略 offset 和 limit。调用后立即停止。
```

Explicit range:

```text
只调用一次内置 read 工具，不要使用其他工具。参数必须精确为 filePath="ABS_FILE", offset=5, limit=7。调用后立即停止。
```

Offset only:

```text
只调用一次内置 read 工具。参数必须包含 filePath="ABS_FILE" 和 offset=10，并且省略 limit。不要使用其他工具，调用后停止。
```

Limit only:

```text
只调用一次内置 read 工具。参数必须包含 filePath="ABS_FILE" 和 limit=6，并且省略 offset。不要使用其他工具，调用后停止。
```

Directory read, supported by OpenCode's current read implementation:

```text
只调用一次 OpenCode 内置 read 工具读取目录，不要调用 glob 或 bash。参数精确使用 filePath="ABS_DIR", offset=1, limit=5。调用后停止。
```

Relative-path behavior:

```text
只调用一次 OpenCode 内置 read 工具，参数只包含 filePath="README.md"。不要先调用任何定位或搜索工具。调用后停止。
```

Failure path:

```text
只调用一次内置 read 工具，参数只包含 filePath="/tmp/auditcov-read-probe-file-that-does-not-exist"。即使文件不存在也必须调用，不要改用其他工具。调用后停止。
```

## Batch prompt

Use this after the individual cases work. Models may parallelize calls, which is useful for verifying that the JSONL append behavior remains intact:

```text
请完成下面 4 次内置文件读取，除此以外不要调用任何工具。每次都必须真的调用 Read/read，不能根据前一次结果回答：
1. ABS_FILE，全文件形式，省略 offset 和 limit；
2. ABS_FILE，offset=1, limit=3；
3. ABS_FILE，offset=4, limit=5；
4. 不存在的路径 /tmp/auditcov-read-probe-file-that-does-not-exist。
完成四次调用后停止，不要总结内容。
```

For Claude Code write `Read` and `file_path`; for OpenCode write `read` and `filePath` if the client does not infer the intended parameter spelling from this generic prompt.

## Inspect results

Show all events:

```bash
python3 scripts/show_read_hook_probe_log.py
```

Filter by client or show only the newest records:

```bash
python3 scripts/show_read_hook_probe_log.py --client claude-code
python3 scripts/show_read_hook_probe_log.py --client opencode --tail 10
```

Show only successful completion records:

```bash
python3 scripts/show_read_hook_probe_log.py --client claude-code --phase after
python3 scripts/show_read_hook_probe_log.py --client opencode --phase after
```

For a successful read, expect two records with the same `session_id` and `call_id`: a `before/attempted` record followed by an `after` record containing `tool_result`. Claude labels that record `succeeded`; OpenCode labels it `completed`. A missing-file test has no Claude success record. OpenCode may omit its after record or expose an error-shaped completed result depending on the tool path, and neither form should count as coverage.

Normalized records contain `probe_client`, `session_id`, `call_id`, `tool_name`, `read_parameters`, and the original hook input where available.

## Uninstall

```bash
python3 scripts/install_read_hook_probes_wsl.py uninstall
```

The installer removes only its managed Claude handler and its two installed hook files. The shared JSONL log is intentionally preserved.
