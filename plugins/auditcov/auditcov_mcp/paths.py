from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from auditcov_mcp import __version__

CONFIG_FILENAME = ".auditcov-config.json"
DEFAULT_WORK_DIR_NAME = ".auditcov"
DB_FILENAME = "auditcov.sqlite3"


class WorkDirError(Exception):
    """Expected work directory configuration error."""


def install_root() -> Path:
    return Path(__file__).resolve().parent.parent


def config_path(root: Path | None = None) -> Path:
    return (root or install_root()) / CONFIG_FILENAME


def default_work_dir(root: Path | None = None) -> Path:
    return (root or install_root()) / DEFAULT_WORK_DIR_NAME


def configured_work_dir(root: Path | None = None) -> Path:
    env_work_dir = os.environ.get("AUDITCOV_WORK_DIR")
    if env_work_dir:
        return Path(env_work_dir).expanduser().resolve()

    path = config_path(root)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        configured = data.get("work_dir")
        if isinstance(configured, str) and configured:
            return Path(configured).expanduser().resolve()

    return default_work_dir(root).resolve()


def default_db_path(root: Path | None = None) -> Path:
    configured_db = os.environ.get("AUDITCOV_DB")
    if configured_db:
        return Path(configured_db).expanduser().resolve()
    return configured_work_dir(root) / DB_FILENAME


def workdir_settings(
    root: Path | None = None, explicit_db_path: Path | None = None
) -> dict[str, Any]:
    actual_root = root or install_root()
    env_db = os.environ.get("AUDITCOV_DB")
    env_work_dir = os.environ.get("AUDITCOV_WORK_DIR")
    work_dir = configured_work_dir(actual_root)
    db_path = explicit_db_path.expanduser().resolve() if explicit_db_path else default_db_path(actual_root)
    override_reason = None

    if explicit_db_path is not None:
        override_reason = "web viewer was started with --db"
    elif env_db:
        override_reason = "AUDITCOV_DB is set"
    elif env_work_dir:
        override_reason = "AUDITCOV_WORK_DIR is set"

    return {
        "version": __version__,
        "install_root": str(actual_root.resolve()),
        "config_path": str(config_path(actual_root).resolve()),
        "work_dir": str(work_dir),
        "default_work_dir": str(default_work_dir(actual_root).resolve()),
        "db_path": str(db_path),
        "can_update_work_dir": override_reason is None,
        "override_reason": override_reason,
    }


def change_work_dir(new_work_dir: str, root: Path | None = None) -> dict[str, Any]:
    if os.environ.get("AUDITCOV_DB"):
        raise WorkDirError("cannot change work directory while AUDITCOV_DB is set")
    if os.environ.get("AUDITCOV_WORK_DIR"):
        raise WorkDirError("cannot change work directory while AUDITCOV_WORK_DIR is set")

    actual_root = root or install_root()
    target = Path(new_work_dir).expanduser().resolve()
    source = configured_work_dir(actual_root)
    if source == target:
        write_config(actual_root, target)
        return {"moved": False, **workdir_settings(actual_root)}

    if is_nested_path(target, source) or is_nested_path(source, target):
        raise WorkDirError("new work directory must not be inside the current work directory")

    try:
        move_work_dir(source, target)
        write_config(actual_root, target)
    except OSError as exc:
        raise WorkDirError(
            "work directory is busy or cannot be moved right now; "
            "stop running AuditCov MCP/Web processes and retry"
        ) from exc

    return {"moved": True, **workdir_settings(actual_root)}


def write_config(root: Path, work_dir: Path) -> None:
    payload = {"work_dir": str(work_dir)}
    path = config_path(root)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def move_work_dir(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return

    if target.exists() and any(target.iterdir()):
        raise WorkDirError("target work directory already exists and is not empty")

    if target.exists():
        target.rmdir()

    shutil.move(str(source), str(target))


def is_nested_path(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return child != parent
    except ValueError:
        return False
