---
name: auditcov
description: Use AuditCov only when the user explicitly names AuditCov, this skill, the AuditCov MCP, audit coverage, objective read coverage, or asks to audit until a specific code-audit coverage threshold is reached. Do not use for ordinary code review or security audit requests that do not mention coverage or AuditCov.
---

# AuditCov

AuditCov tracks objective read coverage: complete source-code lines returned to the model through the AuditCov MCP read tool. It does not prove that a vulnerability audit is complete, and it does not prove the model understood every returned line.

## Activation Rule

只有当用户点名使用该skill / mcp的时候才使用，否则不要使用。

Treat these as explicit activation:

- The user names `AuditCov`, this skill, or the AuditCov MCP.
- The user asks for audit coverage, objective read coverage, or code-audit coverage tracking.
- The user asks to audit until a concrete coverage threshold is reached, such as 80%.

Do not use AuditCov for a normal security review, code audit, bug hunt, or repository exploration unless the user explicitly asks for AuditCov or audit coverage.

## Coverage Goal Rule

如果用户明确要求审计覆盖率，如：审计代码直到覆盖80%/确保审计覆盖率达到80%这种话术，则需要使用goal设置一个目标，一直审计直到完成目标，如果用户没有明确要求一定要审计达到特定覆盖率，则不受影响，覆盖率仅供用户参考，不作为目标，审计节奏由你自由掌控

When a concrete coverage threshold is requested:

1. Create a goal before starting the audit, with an objective such as `Audit target code until AuditCov objective read coverage is at least 80% and report security findings`.
2. Initialize AuditCov for the user-approved audit scope.
3. Continue reading target files through `auditcov_read_file`, analyzing returned code, and checking `auditcov_get_coverage` until the requested coverage threshold is met.
4. Do not mark the goal complete until the threshold is actually reached, or until a real blocker prevents progress.

When no concrete threshold is requested, use coverage as a reference signal only. Do not turn coverage into an implicit completion gate.

## MCP Workflow

Use only the AuditCov MCP tools for reads that should count toward objective coverage:

- `auditcov_init_project`: freeze the denominator for the current thread.
- `auditcov_read_file`: read complete file lines and record objective read coverage.
- `auditcov_get_coverage`: check project, directory, or file coverage.
- `auditcov_get_file_detail`: inspect covered and uncovered line ranges for one file.

Recommended sequence:

1. Determine the repository root and target paths from the user request. Do not shrink the target denominator to improve coverage.
2. Call `auditcov_init_project` once for the chosen scope.
3. Use shell commands only for discovery and search. Shell reads do not count toward AuditCov objective read coverage.
4. Use `auditcov_read_file` for source code that should count as read coverage. If the result is truncated, continue from `next_start_line`.
5. Use `auditcov_get_coverage` and `auditcov_get_file_detail` to choose remaining unread files or ranges.
6. Report coverage as objective read coverage, not as proof that the audit is complete.

If the AuditCov MCP tools are unavailable, say that AuditCov is not configured in this Codex environment. Do not pretend shell reads count as AuditCov coverage.
