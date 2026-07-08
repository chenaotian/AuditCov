# Project Baseline

This repository is for the AI Audit Coverage / AuditCov project.

Primary design document:

- `ai_audit_coverage_codex_v1_design.md`

## Working Rules

- Use Git to manage every project update.
- Check `git status` before editing.
- Do not overwrite user changes unless explicitly requested.
- Keep changes scoped to the current request.
- Commit completed updates with a clear commit message.
- Leave the working tree clean after each completed task unless the user asks otherwise.

## Implementation Direction

- Follow the Codex v1 design document unless the user updates the design.
- Prefer conservative, auditable behavior over optimistic coverage claims.
- Treat objective read coverage, snippet exposure, and file discovery as separate concepts.
- Do not let model-controlled inputs lower project coverage gates or shrink the target denominator.
