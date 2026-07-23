from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Sequence

from auditcov_mcp import __version__
from auditcov_mcp.client import AuditCovClient, AuditCovClientError

DEFAULT_TIMEOUT_SECONDS = 300.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auditcov",
        description="Manage AuditCov projects and query objective read coverage.",
    )
    parser.add_argument("--version", action="version", version=f"AuditCov {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    project = commands.add_parser("project", help="Manage tracked repository projects.")
    project_commands = project.add_subparsers(dest="project_command", required=True)

    create = project_commands.add_parser(
        "create", help="Create a project and freeze its source snapshot."
    )
    create.add_argument("repository_root", help="Repository root visible to the server.")
    create.add_argument("--name", help="Optional project display name.")
    add_output_argument(create)
    add_connection_arguments(create)
    create.set_defaults(handler=create_project)

    list_projects = project_commands.add_parser(
        "list", help="List projects known to the server."
    )
    list_projects.add_argument(
        "--sessions",
        action="store_true",
        help="Also fetch and print the internal and native session IDs for each project.",
    )
    add_output_argument(list_projects)
    add_connection_arguments(list_projects)
    list_projects.set_defaults(handler=show_projects)

    delete = project_commands.add_parser(
        "delete", help="Permanently delete a project and all of its AuditCov data."
    )
    delete.add_argument("project_id", type=positive_int, help="Numeric project ID.")
    delete.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="Confirm permanent deletion without an interactive prompt.",
    )
    add_output_argument(delete)
    add_connection_arguments(delete)
    delete.set_defaults(handler=delete_project)

    coverage = commands.add_parser(
        "coverage", help="Get aggregate objective read coverage for a project."
    )
    coverage.add_argument("project_id", type=positive_int, help="Numeric project ID.")
    selection = coverage.add_mutually_exclusive_group()
    selection.add_argument(
        "--session-id",
        dest="session_ids",
        action="append",
        type=positive_int,
        metavar="ID",
        help=(
            "Include one internal session ID; repeat to combine exact sessions. "
            "Parent and child sessions remain independent."
        ),
    )
    selection.add_argument(
        "--no-sessions",
        action="store_true",
        help="Select no sessions and report an empty coverage numerator.",
    )
    coverage.add_argument(
        "--tree",
        action="store_true",
        help=(
            "Fetch the complete file tree. Without this option, coverage uses the "
            "lightweight summary; all-session totals use materialized data."
        ),
    )
    add_output_argument(coverage)
    add_connection_arguments(coverage)
    coverage.set_defaults(handler=show_coverage)
    return parser


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--server-url",
        default=None,
        help=(
            "AuditCov Server URL. Defaults to AUDITCOV_SERVER_URL or "
            "http://127.0.0.1:8765."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"Request timeout (default: {DEFAULT_TIMEOUT_SECONDS:g} seconds).",
    )


def add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def client_for(args: argparse.Namespace) -> AuditCovClient:
    return AuditCovClient(base_url=args.server_url, timeout=args.timeout)


def create_project(args: argparse.Namespace) -> dict[str, Any]:
    payload = client_for(args).post(
        "/api/projects",
        {"project_root": args.repository_root, "name": args.name},
    )
    if args.json:
        print_json(payload)
    else:
        print(f"Created project {payload['id']}: {payload['name']}")
        print(f"Root: {payload['project_root']}")
        print(f"Snapshot: {payload['snapshot_id']}")
        print(
            f"Source snapshot: {payload['total_files']} files, "
            f"{payload['total_lines']} lines"
        )
    return payload


def show_projects(args: argparse.Namespace) -> dict[str, Any]:
    client = client_for(args)
    payload = client.get("/api/projects", {})
    if args.sessions:
        payload = {
            **payload,
            "projects": [
                {
                    **project,
                    "sessions": client.get(f"/api/projects/{project['id']}", {}).get(
                        "sessions", []
                    ),
                }
                for project in payload.get("projects", [])
            ],
        }
    if args.json:
        print_json(payload)
        return payload
    projects = payload.get("projects", [])
    if not projects:
        print("No AuditCov projects found.")
        return payload
    for project in projects:
        print(
            f"{project['id']}\t{project['name']}\t{format_percent(project)}\t"
            f"{project['session_count']} sessions\t{project['project_root']}"
        )
        for session in project.get("sessions", []):
            parent = session.get("parent_session_id") or "-"
            title = session.get("session_title") or "-"
            print(
                f"  session {session['id']}\t{session['agent_type']}\t"
                f"{session['agent_session_id']}\tparent={parent}\t"
                f"{format_percent(session)}\t{title}"
            )
    return payload


def delete_project(args: argparse.Namespace) -> dict[str, Any]:
    payload = client_for(args).delete(f"/api/projects/{args.project_id}")
    if args.json:
        print_json(payload)
    else:
        print(f"Deleted project {payload['id']}: {payload['name']}")
        print(f"Root: {payload['project_root']}")
        print("AuditCov snapshot, sessions, reads, and coverage records were removed.")
        print("Repository files were not deleted.")
    return payload


def show_coverage(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.no_sessions:
        params["selection"] = "none"
    elif args.session_ids:
        params["session_id"] = args.session_ids
    dynamic = bool(args.tree)
    endpoint = (
        f"/api/projects/{args.project_id}/coverage"
        if dynamic
        else f"/api/projects/{args.project_id}/coverage-summary"
    )
    payload = client_for(args).get(endpoint, params)
    if args.json:
        print_json(payload)
    else:
        selected = payload.get("selected_session_ids")
        selected_label = (
            "all"
            if payload.get("selection") == "all" and selected is None
            else ", ".join(str(item) for item in (selected or [])) or "none"
        )
        print(f"Project {payload['id']}: {payload['name']}")
        print(f"Selected sessions: {selected_label}")
        print(
            f"Coverage: {format_percent(payload)} "
            f"({payload['covered_lines']} / {payload['total_lines']} lines)"
        )
        print(
            f"Files with coverage: {payload['covered_files']} / "
            f"{payload['total_files']}"
        )
    return payload


def format_percent(payload: dict[str, Any]) -> str:
    return f"{float(payload.get('percent', 0.0)):.2f}%"


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except (AuditCovClientError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"auditcov: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
