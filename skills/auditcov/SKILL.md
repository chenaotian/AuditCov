---
name: auditcov
description: Use AuditCov only when the user explicitly names AuditCov, this skill, the AuditCov MCP, audit coverage, objective read coverage, or asks to audit until a specific code-audit coverage threshold is reached. Do not use for ordinary code review or security audit requests that do not mention coverage or AuditCov.
---

# AuditCov

AuditCov tracks objective read coverage: complete source-code lines returned to the model through the AuditCov MCP read tool. It does not prove that a vulnerability audit is complete, and it does not prove the model understood every returned line.

## Activation Rule

Use AuditCov only when the user explicitly asks for the AuditCov skill or AuditCov MCP.

Treat these as explicit activation:

- The user names `AuditCov`, this skill, or the AuditCov MCP.
- The user asks for audit coverage, objective read coverage, or code-audit coverage tracking.
- The user asks to audit until a concrete coverage threshold is reached, such as 80%.

Do not use AuditCov for a normal security review, code audit, bug hunt, or repository exploration unless the user explicitly asks for AuditCov or audit coverage.

## Initialization Rule

Call `auditcov_init_project` exactly once when the current request first activates AuditCov for the thread. Do not call it repeatedly to refresh state, reset coverage, or improve the denominator. If AuditCov is already initialized for the current thread, continue using the existing project. Reinitialize only when the user explicitly starts a new AuditCov audit scope.

After initialization, do not shrink or replace the target paths to make coverage easier to reach.

## Coverage Goal Rule

If the user explicitly asks to audit until a coverage target is reached, such as auditing until 80% coverage or ensuring audit coverage reaches 80%, create a goal and keep auditing until that target is complete. If the user does not request a specific coverage target, treat coverage as informational only and let the audit proceed at the normal pace.

When a concrete coverage threshold is requested:

1. Create a goal before starting the audit, with an objective such as `Audit target code until AuditCov objective read coverage is at least 80% and report security findings`.
2. Initialize AuditCov for the user-approved audit scope.
3. Continue reading target files through `auditcov_read_file`, analyzing returned code, and checking `auditcov_get_coverage` until the requested coverage threshold is met.
4. Do not mark the goal complete until the threshold is actually reached, or until a real blocker prevents progress.

When no concrete threshold is requested, use coverage as a reference signal only. Do not turn coverage into an implicit completion gate.

## Code Reading Rule

After AuditCov is activated, use `auditcov_read_file` for file and line-range reads that provide source code for audit review or coverage. Do not use shell commands or other tools such as `cat`, `type`, `Get-Content`, `sed -n`, `head`, `tail`, or `less` to dump source files or line ranges as a substitute for `auditcov_read_file`.

Code search is allowed. Shell commands and search tools may print matching lines or small snippets to locate candidate files, functions, symbols, or patterns. Search output does not count as AuditCov coverage; before using a searched code region as audit evidence, read the relevant file or range with `auditcov_read_file`.

## MCP Workflow

Use only the AuditCov MCP tools for reads that should count toward objective coverage:

- `auditcov_init_project`: freeze the denominator for the current thread.
- `auditcov_read_file`: read complete file lines and record objective read coverage.
- `auditcov_get_coverage`: check project, directory, or file coverage.
- `auditcov_get_file_detail`: inspect covered and uncovered line ranges for one file.

Recommended sequence:

1. Determine the repository root and target paths from the user request. Do not shrink the target denominator to improve coverage.
2. Call `auditcov_init_project` once for the chosen scope when AuditCov is first activated.
3. Use shell commands for discovery and code search as needed. Search snippets do not count as coverage.
4. Use `auditcov_read_file` for source code that should count as read coverage. If the result is truncated, continue from `next_start_line`.
5. Use `auditcov_get_coverage` and `auditcov_get_file_detail` to choose remaining unread files or ranges.
6. Report coverage as objective read coverage, not as proof that the audit is complete.

If the AuditCov MCP tools are unavailable, say that AuditCov is not configured in this Codex environment. Do not pretend shell reads count as AuditCov coverage.
