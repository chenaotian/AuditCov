---
name: auditcov
description: Use AuditCov only when the user explicitly names AuditCov, objective read coverage, audit coverage, or asks to audit until a specific coverage threshold is reached. Do not activate it for ordinary code review or security audit requests that do not mention coverage or AuditCov.
---

# AuditCov

AuditCov tracks objective source-read coverage across Codex, Claude Code, and OpenCode. Coverage means complete source lines successfully returned through a tracked Read path. It does not prove understanding or audit completeness.

## Prerequisite

The repository must already be created as a project in the local AuditCov Web UI. If a tool reports that the path is not part of a configured project, ask the user to start `python -m auditcov_mcp.web`, open `http://127.0.0.1:8765`, and create the repository project. There is no MCP initialization tool.

Never try to create, shrink, or replace the project denominator through model-controlled tool arguments.

## Activation

Activate AuditCov only when the user names AuditCov or explicitly requests objective audit/read coverage. If the user requests a concrete coverage threshold, create a goal and continue until the threshold is reached or a real blocker prevents progress. Without a concrete threshold, treat coverage as informational.

## Codex reading rule

For source content used in the audit, call `auditcov_read_file`. Do not substitute `cat`, `type`, `Get-Content`, `sed`, `head`, `tail`, or another direct file dump. Code search is allowed for discovery, but search snippets do not count as coverage; read evidence through `auditcov_read_file`.

The Codex MCP exposes:

- `auditcov_read_file`: read tracked complete source lines through the central server.
- `auditcov_get_coverage`: query project, directory, or file coverage for the current Codex `thread_id`.
- `auditcov_get_file_detail`: inspect covered and uncovered ranges for one file.

If a read is truncated, continue from `next_start_line`. Use coverage and file detail to prioritize remaining unread areas.

## Web semantics

Each Web project has one frozen whole-repository source snapshot shared by every Agent session. Parent sessions can be expanded to show Claude Code or OpenCode subagents. Parent and child checkboxes are independent: selecting a parent includes only that parent's own reads, and a child must be selected separately to include its reads.

For exactly the selected parent and child sessions, the numerator is the union of their successful covered ranges and the denominator remains the project's single frozen snapshot. Before-hook attempts do not count. Reads outside configured projects are ignored.

Always describe the metric as objective read coverage, never as proof that the security audit is complete.
