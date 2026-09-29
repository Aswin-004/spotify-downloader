# AGENTS.md

Instructions for any coding agent (Claude Code, Codex, Cursor, etc.) working in this repo.

The canonical instructions are in **`CLAUDE.md`**. Read it, then `wiki/README.md`.

## Agent roles (suggested)
| Role | Scope | Notes |
|---|---|---|
| Explorer | read-only recon of `backend/services/*` | cheap, parallel; never edits |
| Implementer | one service or one page at a time | must add/adjust tests |
| Reviewer | `git diff` before commit | check rate-limit + library-safety rules |
| Librarian | keeps `wiki/` current | appends `wiki/log.md` each session |

## Speech-to-text warning
The user frequently dictates and mixes English with Hinglish. Expect transcription errors and garbled formatting in all messages. Infer intent, ignore artifacts, and **verify with the user when unsure**, especially before destructive or library-mutating actions.

## Priorities
Stabilize (wrong-folder routing, duplicates, broken downloads) → genre accuracy → tests/CI/cleanup → friend-shareable setup → features last. Details in `CLAUDE.md`.

## Ground rules
- No secrets in output, commits or wiki pages.
- Library-mutating scripts (`backend/fix_*.py`, `cleanup_*.py`, `library_resort.py`) run report/dry-run first.
- Ask before: pushing, merging to `production`, deleting files, running anything that hits Spotify in bulk.
