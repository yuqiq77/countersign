# CLAUDE.md

Read `AGENTS.md` (the shared working agreement, constraints, and
orientation) and then `docs/STATUS.md`. Nothing in this file overrides
`AGENTS.md`; it only adds what is specific to Claude Code.

## Environment (Claude Code only)

- The desktop workspace resets the shell's working directory between
  commands. Use absolute paths under the repository root and never rely on
  a `cd` persisting to the next command.
- Local checks run from `.venv/bin/` (`ruff`, `pyright`, `lint-imports`,
  `pytest`), created with `pip install -e ".[dev]"` as CI does.
- `pnpm` lives in `~/Library/pnpm/` and is not on the non-interactive
  shell's PATH (`corepack` is absent too). Call it by full path, or run
  `apps/web/node_modules/.bin/next` and `tsc` directly.

## Session memory (Claude Code only)

Claude Code keeps an auto-memory for this project outside the repository: a
few topic files plus an index. It complements the repo docs; it never
duplicates them.

- The project-pointers memory holds reading order and **one** "current
  state" line that defers to `docs/STATUS.md`. Overwrite it; never append a
  dated entry — history lives in the repo (git log, ADRs).
- Save only what the repo does not record: environment traps, owner
  preferences and corrections, distilled lessons, standing decisions. A fact
  `docs/STATUS.md` or an ADR already states is a pointer, not a memory.
- Operator-private context belongs in `HANDOFF.md` (gitignored), not in
  memory and never in tracked files.
