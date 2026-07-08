# Project Baseline

This repository is for the AI Audit Coverage / AuditCov project.

Current baseline:

- Keep the first version small and runnable.
- Add design detail only when it directly guides the next implementation step.
- Do not recreate the deleted long-form v1 design unless the user asks for it.

## Working Rules

- Use Git to manage every project update.
- Check `git status` before editing.
- Do not overwrite user changes unless explicitly requested.
- Keep changes scoped to the current request.
- Commit completed updates with a clear commit message.
- Leave the working tree clean after each completed task unless the user asks otherwise.
- When updating `skills/auditcov/SKILL.md`, update the human-readable Chinese mirror at `skills/auditcov/SKILL.zh-CN.md` in the same change.

## Implementation Direction

- Build toward a minimal Codex-first AuditCov prototype.
- Prefer conservative, auditable behavior over optimistic coverage claims.
- Treat objective read coverage, snippet exposure, and file discovery as separate concepts.
- Do not let model-controlled inputs lower project coverage gates or shrink the target denominator.
