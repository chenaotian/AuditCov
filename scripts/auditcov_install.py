#!/usr/bin/env python3
"""Install or uninstall AuditCov adapters for Codex, Claude Code, and OpenCode."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MARKER = "AuditCov Read tracking"
OPENCODE_FILENAME = "auditcov_plugin.ts"
CODEX_MARKETPLACE_NAME = "auditcov-local"


def data_home() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def claude_installed_hook() -> Path:
    return data_home() / "auditcov" / "adapters" / "claude_code_hook.py"


def opencode_installed_plugin() -> Path:
    return config_home() / "opencode" / "plugins" / OPENCODE_FILENAME


def codex_runtime_marketplace() -> Path:
    return data_home() / "auditcov" / "codex-marketplace"


def load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object in {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hook_command(path: Path) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([sys.executable, str(path)])
    return " ".join(shlex.quote(item) for item in (sys.executable, str(path)))


def is_auditcov_handler(handler: Any) -> bool:
    return (
        isinstance(handler, dict)
        and handler.get("type") == "command"
        and (
            handler.get("statusMessage") == CLAUDE_MARKER
            or "auditcov_hook.py" in str(handler.get("command", ""))
        )
    )


def remove_claude_handlers(settings: dict[str, Any]) -> int:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in ("PreToolUse", "PostToolUse"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        retained_groups = []
        for group in groups:
            if not isinstance(group, dict):
                retained_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                retained_groups.append(group)
                continue
            retained = [handler for handler in handlers if not is_auditcov_handler(handler)]
            removed += len(handlers) - len(retained)
            if retained:
                updated = dict(group)
                updated["hooks"] = retained
                retained_groups.append(updated)
        if retained_groups:
            hooks[event] = retained_groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    return removed


def install_claude() -> None:
    source = ROOT / "hooks" / "claude_code" / "auditcov_hook.py"
    target = claude_installed_hook()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    settings_path = claude_settings_path()
    settings = load_json_object(settings_path)
    remove_claude_handlers(settings)
    hooks = settings.setdefault("hooks", {})
    command = hook_command(target)
    for event in ("PreToolUse", "PostToolUse"):
        hooks.setdefault(event, []).append(
            {
                "matcher": "Read",
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 10,
                        "statusMessage": CLAUDE_MARKER,
                    }
                ],
            }
        )
    write_json(settings_path, settings)
    print(f"installed Claude Code hooks: {target}")


def uninstall_claude() -> None:
    path = claude_settings_path()
    if path.is_file():
        settings = load_json_object(path)
        removed = remove_claude_handlers(settings)
        write_json(path, settings)
        print(f"removed {removed} Claude Code hook handlers")
    target = claude_installed_hook()
    if target.is_file():
        target.unlink()


def install_opencode() -> None:
    source = ROOT / "hooks" / "opencode" / OPENCODE_FILENAME
    target = opencode_installed_plugin()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"installed OpenCode plugin: {target}")


def uninstall_opencode() -> None:
    target = opencode_installed_plugin()
    if target.is_file():
        target.unlink()
        print(f"removed OpenCode plugin: {target}")


def run_codex(*args: str, check: bool = True) -> bool:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable was not found on PATH")
    result = subprocess.run(
        [executable, *args],
        check=False,
        stdout=None if check else subprocess.DEVNULL,
        stderr=None if check else subprocess.DEVNULL,
    )
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, [executable, *args])
    return result.returncode == 0


def prepare_codex_marketplace() -> Path:
    source_plugin = ROOT / "plugins" / "auditcov"
    source_marketplace = ROOT / ".agents" / "plugins" / "marketplace.json"
    if not source_plugin.is_dir():
        raise RuntimeError(f"AuditCov plugin is missing: {source_plugin}")
    if not source_marketplace.is_file():
        raise RuntimeError(f"AuditCov marketplace is missing: {source_marketplace}")

    runtime_root = codex_runtime_marketplace()
    runtime_plugin = runtime_root / "plugins" / "auditcov"
    shutil.copytree(
        source_plugin,
        runtime_plugin,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    runtime_marketplace = runtime_root / ".agents" / "plugins" / "marketplace.json"
    runtime_marketplace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_marketplace, runtime_marketplace)

    mcp_path = runtime_plugin / ".mcp.json"
    mcp_config = load_json_object(mcp_path)
    servers = mcp_config.get("mcpServers")
    auditcov = servers.get("auditcov") if isinstance(servers, dict) else None
    if not isinstance(auditcov, dict):
        raise RuntimeError(f"AuditCov MCP configuration is invalid: {mcp_path}")
    auditcov["command"] = str(Path(sys.executable).resolve())
    write_json(mcp_path, mcp_config)
    return runtime_root


def install_codex() -> None:
    marketplace_root = prepare_codex_marketplace()
    run_codex("plugin", "marketplace", "remove", CODEX_MARKETPLACE_NAME, check=False)
    run_codex("plugin", "marketplace", "add", str(marketplace_root))
    run_codex("plugin", "add", f"auditcov@{CODEX_MARKETPLACE_NAME}")
    print(
        f"installed Codex plugin: auditcov@{CODEX_MARKETPLACE_NAME} "
        f"using {Path(sys.executable).resolve()}"
    )


def uninstall_codex() -> None:
    run_codex("plugin", "remove", "auditcov")
    run_codex("plugin", "marketplace", "remove", CODEX_MARKETPLACE_NAME, check=False)
    print("removed Codex plugin: auditcov")


def selected_agents(args: argparse.Namespace) -> list[str]:
    if args.all:
        return ["codex", "claude", "opencode"]
    selected = [name for name in ("codex", "claude", "opencode") if getattr(args, name)]
    if not selected:
        raise RuntimeError("select --codex, --claude, --opencode, or --all")
    return selected


def status() -> None:
    settings = load_json_object(claude_settings_path())
    hooks_text = json.dumps(settings.get("hooks", {}), ensure_ascii=False)
    print(f"claude-code: {'installed' if CLAUDE_MARKER in hooks_text else 'not installed'}")
    print(f"opencode: {'installed' if opencode_installed_plugin().is_file() else 'not installed'}")
    print("codex: use 'codex plugin list' to inspect the current Codex installation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--claude", action="store_true")
    parser.add_argument("--opencode", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.action == "status":
            status()
            return
        agents = selected_agents(args)
        actions = {
            "install": {
                "codex": install_codex, "claude": install_claude, "opencode": install_opencode,
            },
            "uninstall": {
                "codex": uninstall_codex, "claude": uninstall_claude, "opencode": uninstall_opencode,
            },
        }
        for agent in agents:
            actions[args.action][agent]()
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"auditcov installer error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
