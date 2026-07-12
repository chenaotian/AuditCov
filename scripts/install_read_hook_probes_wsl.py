#!/usr/bin/env python3
"""Install Read-parameter hooks for Claude Code and OpenCode on WSL/Linux."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

STATUS_MESSAGE = "Recording Read parameters for the AuditCov probe"
OPENCODE_MARKER = "AuditCov Read hook probe. This marker is used by the installer"


def data_home() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "share"


def state_home() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".local" / "state"


def config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".config"


def install_paths() -> dict[str, Path]:
    install_dir = data_home() / "auditcov-read-hook-probe"
    return {
        "install_dir": install_dir,
        "claude_hook": install_dir / "claude_read_hook.py",
        "claude_settings": Path.home() / ".claude" / "settings.json",
        "opencode_plugin": config_home()
        / "opencode"
        / "plugins"
        / "auditcov_read_hook_probe.ts",
        "log": state_home() / "auditcov-read-hook-probe" / "events.jsonl",
    }


def source_paths() -> dict[str, Path]:
    root = Path(__file__).resolve().parent.parent
    return {
        "claude_hook": root / "hooks" / "read_probe" / "claude_read_hook.py",
        "opencode_plugin": root / "hooks" / "read_probe" / "opencode_read_hook.ts",
    }


def managed_handler(claude_hook: Path, log_path: Path) -> dict[str, Any]:
    return {
        "type": "command",
        # Claude Code 2.1.138 accepts the args field in settings but launches only
        # the bare executable. Keep the complete invocation in shell form so the
        # recorder also works on clients that do not implement exec-form args.
        "command": (
            f"python3 {shlex.quote(str(claude_hook))} "
            f"--log {shlex.quote(str(log_path))}"
        ),
        "timeout": 10,
        "statusMessage": STATUS_MESSAGE,
    }


def is_managed_handler(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("statusMessage") != STATUS_MESSAGE:
        return False

    # Recognize the old exec-form configuration so reinstall and uninstall can
    # replace it without disturbing unrelated user hooks.
    args = value.get("args")
    if isinstance(args, list) and args and isinstance(args[0], str):
        script_path = Path(args[0])
        return (
            script_path.name == "claude_read_hook.py"
            and script_path.parent.name == "auditcov-read-hook-probe"
        )

    command = value.get("command")
    if not isinstance(command, str):
        return False
    try:
        command_parts = shlex.split(command)
    except ValueError:
        return False
    if len(command_parts) < 2:
        return False
    script_path = Path(command_parts[1])
    return (
        script_path.name == "claude_read_hook.py"
        and script_path.parent.name == "auditcov-read-hook-probe"
    )


def remove_managed_handlers(settings: dict[str, Any]) -> int:
    hooks = settings.get("hooks")
    if hooks is None:
        return 0
    if not isinstance(hooks, dict):
        raise RuntimeError("Claude settings 'hooks' must be an object")
    groups = hooks.get("PreToolUse")
    if groups is None:
        return 0
    if not isinstance(groups, list):
        raise RuntimeError("Claude settings hooks.PreToolUse must be an array")

    removed = 0
    retained_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            retained_groups.append(group)
            continue
        handlers = group["hooks"]
        retained_handlers = [handler for handler in handlers if not is_managed_handler(handler)]
        removed += len(handlers) - len(retained_handlers)
        if retained_handlers:
            retained_groups.append({**group, "hooks": retained_handlers})
        elif set(group) - {"matcher", "hooks"}:
            retained_groups.append({**group, "hooks": []})

    if retained_groups:
        hooks["PreToolUse"] = retained_groups
    else:
        hooks.pop("PreToolUse", None)
    return removed


def add_managed_handler(
    settings: dict[str, Any], claude_hook: Path, log_path: Path
) -> None:
    remove_managed_handlers(settings)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError("Claude settings 'hooks' must be an object")
    groups = hooks.setdefault("PreToolUse", [])
    if not isinstance(groups, list):
        raise RuntimeError("Claude settings hooks.PreToolUse must be an array")
    groups.append(
        {
            "matcher": "Read",
            "hooks": [managed_handler(claude_hook, log_path)],
        }
    )


def read_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Claude settings are not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Claude settings root must be an object: {path}")
    return value


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".auditcov-read-hook.tmp")
    temporary.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def install() -> None:
    if shutil.which("python3") is None:
        raise RuntimeError("python3 is not available in PATH")
    if shutil.which("claude") is None:
        raise RuntimeError("claude is not available in PATH")
    if shutil.which("opencode") is None:
        raise RuntimeError("opencode is not available in PATH")

    sources = source_paths()
    paths = install_paths()
    for source in sources.values():
        if not source.is_file():
            raise RuntimeError(f"source file is missing: {source}")

    plugin_path = paths["opencode_plugin"]
    if plugin_path.is_file():
        existing = plugin_path.read_text(encoding="utf-8", errors="replace")
        if OPENCODE_MARKER not in existing:
            raise RuntimeError(f"refusing to overwrite unrelated file: {plugin_path}")

    paths["install_dir"].mkdir(parents=True, exist_ok=True)
    paths["log"].parent.mkdir(parents=True, exist_ok=True)
    plugin_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sources["claude_hook"], paths["claude_hook"])
    shutil.copy2(sources["opencode_plugin"], plugin_path)
    paths["claude_hook"].chmod(0o755)

    settings = read_settings(paths["claude_settings"])
    add_managed_handler(settings, paths["claude_hook"], paths["log"])
    write_settings(paths["claude_settings"], settings)

    print("Read hook probes installed for Claude Code and OpenCode.")
    print(f"Shared log: {paths['log']}")
    print("Restart both clients before testing.")


def uninstall() -> None:
    paths = install_paths()
    settings_path = paths["claude_settings"]
    if settings_path.is_file():
        settings = read_settings(settings_path)
        if remove_managed_handlers(settings):
            write_settings(settings_path, settings)

    plugin_path = paths["opencode_plugin"]
    if plugin_path.is_file():
        existing = plugin_path.read_text(encoding="utf-8", errors="replace")
        if OPENCODE_MARKER not in existing:
            raise RuntimeError(f"refusing to remove unrelated file: {plugin_path}")
        plugin_path.unlink()

    claude_hook = paths["claude_hook"]
    if claude_hook.is_file():
        claude_hook.unlink()

    print("Read hook probe integrations removed. The shared log was preserved:")
    print(paths["log"])


def status() -> None:
    paths = install_paths()
    settings = read_settings(paths["claude_settings"])
    hooks = settings.get("hooks", {})
    groups = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else []
    handler_count = sum(
        1
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        for handler in group["hooks"]
        if is_managed_handler(handler)
    )
    print(f"Claude hook config: {handler_count} managed handler(s)")
    print(
        f"Claude hook file:   {paths['claude_hook']} "
        f"({'present' if paths['claude_hook'].is_file() else 'missing'})"
    )
    print(
        f"OpenCode plugin:    {paths['opencode_plugin']} "
        f"({'present' if paths['opencode_plugin'].is_file() else 'missing'})"
    )
    print(
        f"Shared log:         {paths['log']} "
        f"({'present' if paths['log'].is_file() else 'not created yet'})"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("install", "uninstall", "status"), nargs="?", default="install"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.action == "install":
            install()
        elif args.action == "uninstall":
            uninstall()
        else:
            status()
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
