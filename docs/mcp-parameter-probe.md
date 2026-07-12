# MCP Parameter Probe for Claude Code and OpenCode

This probe records the exact JSON-RPC messages that Claude Code and OpenCode send to a local stdio MCP server. It is intended to answer whether either client adds fields such as `params._meta` to `tools/call`.

## Install in WSL

From this repository inside WSL, run:

```bash
python3 scripts/install_mcp_parameter_probe_wsl.py install
```

The installer:

- copies the dependency-free Python server to `~/.local/share/auditcov-mcp-probe/`;
- registers `auditcov-parameter-probe` in Claude Code user scope with `claude mcp add-json`;
- creates one OpenCode plugin at `~/.config/opencode/plugins/auditcov_mcp_parameter_probe.ts` that injects the same MCP server without rewriting `opencode.json` or `opencode.jsonc`;
- writes both clients' events to `~/.local/state/auditcov-mcp-probe/events.jsonl` by default.

Restart Claude Code and OpenCode after installation. Check registration with:

```bash
python3 scripts/install_mcp_parameter_probe_wsl.py status
```

## Invoke the probe

In Claude Code, ask:

```text
Call the auditcov-parameter-probe probe_parameters MCP tool exactly once with test_value set to claude-test-001. Then show me the tool result.
```

In OpenCode, ask:

```text
Call the auditcov-parameter-probe probe_parameters MCP tool exactly once with test_value set to opencode-test-001. Then show me the tool result.
```

The tool result includes `received_params`, `received_meta`, and `meta_present`. The durable log contains the entire parsed message and its original single-line JSON text.

## Inspect the result

Show only tool calls:

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path.home() / ".local/state/auditcov-mcp-probe/events.jsonl"
for line in path.read_text(encoding="utf-8").splitlines():
    event = json.loads(line)
    message = event.get("message", {})
    if message.get("method") == "tools/call":
        print(json.dumps(event, ensure_ascii=False, indent=2))
PY
```

Each record has a configured `probe_client` value (`claude-code` or `opencode`) so calls can be compared even if neither client sends identifying metadata.

An absent `_meta` is a valid and important result. Do not infer that the logger dropped it: the logger stores the complete JSON-RPC request before any tool dispatch or schema handling.

## Refresh or uninstall

Refresh the installed copy and Claude registration:

```bash
python3 scripts/install_mcp_parameter_probe_wsl.py install --force
```

Remove the two registrations and the installed server:

```bash
python3 scripts/install_mcp_parameter_probe_wsl.py uninstall
```

Uninstall intentionally preserves the JSONL log. It can contain project paths and client metadata, so review it before sharing.
