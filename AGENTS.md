# AGENTS.md — shared working agreement

Read this first, then `docs/STATUS.md`. This file is the one set of rules
for every agent (Claude, Codex, …) working in this repository; tool-specific
notes live in that tool's own file (`CLAUDE.md`) and must not restate these.

## Working agreement

Follow the user's intent:
- For questions, design discussions, and reviews, provide analysis and
  recommendations. Change files only when requested.
- For implementation and fix requests, carry the work through relevant
  investigation, implementation, validation, and documentation. No separate
  mode-switch confirmation is needed.

Resolve routine, reversible implementation choices using repository context.
Ask when missing information materially changes product behavior, scope,
data handling, or an owner-reserved decision. Continue independent work
while awaiting an answer.

Complete the requested outcome before handing back. Report what changed,
what was actually verified (the commands and their real results), and any
remaining blockers. Do not treat a first implementation or a list of
suggested test steps as completion.

## Orientation

- `docs/STATUS.md` — what is being worked on, what is missing, what comes
  next, what waits on the owner. Rewritten in place; never a changelog.
- `docs/job-search-agent-architecture-v4.md` — the implementation baseline.
  §2 is the Load-Bearing Test every component must pass; §9 the case state
  machine; §24 the timeboxed slices and their kill rules; §25 the gate
  matrix and claim levels; §29 deferred capabilities and their revisit
  conditions.
- `docs/adr/` — the decision ledger; `docs/adr/README.md` is the index.
- `README.md` — the public claims. Its "Current scope" section is the only
  place that states what is implemented, and it never runs ahead of code,
  tests, and a reproduced verification.
- `HANDOFF.md` — operator-private context, gitignored and possibly absent.
  Read it if present; never commit it or quote from it into tracked files.

Three sources of truth, kept distinct:
- **Design intent**: v4 plus the latest accepted ADR.
- **Current implementation**: the code and a reproducible verification.
- **Historical completion**: git history and, from Slice 2, a ticket's
  completion notes.
When they disagree, record the inconsistency where you found it and resolve
it; never let an old completion record stand in for actual behavior.

## Recording (change-driven, not per round)

Update records when implementation, verification results, adopted
decisions, or next actions change; sync only the summaries affected. A
discussion that changes no state produces no routine record.

| Where | What belongs there |
| --- | --- |
| `docs/STATUS.md` | Current focus, blockers, next steps, owner decisions |
| `docs/adr/` | A decision that constrains future work (new file; index entry) |
| `README.md` "Current scope" | What is implemented, in the same terms v4 §25 uses |
| `docs/tickets/` (from Slice 2) | Scope, implementation result, verification evidence, remaining issues |

New tickets stay uncommitted in the working tree until the owner has read
them.

## Project constraints (each one was learned the hard way)

- **Operator-confidential material stays out of the tree** (v4 §21).
  `HANDOFF.md`, `config/operator/`, and `data/operator/` are gitignored
  locations for private context and real policy values. Keep tracked
  files and commit messages depersonalized; review changes for
  confidential data before committing. The operator profile reaches the
  evaluator only through `COUNTERSIGN_OPERATOR_PROFILE_PATH` — there is
  deliberately no default path, so tests and host processes never pick it
  up by accident. Persist its 8-hex fingerprint as policy identity; never
  separately persist or log the raw profile text.
- **Temporal test environments need `pydantic_data_converter`.** Contracts
  are `strict=True`; the SDK's default converter validates in Python mode
  and rejects enum-typed fields. Use `time_skipping_env()` from
  `tests/workflow/support.py`, never a bare `WorkflowEnvironment`.
- **SQLite masks what PostgreSQL catches**: VARCHAR widths and timezone
  round-trips. `pytest` alone is not green. Run it with
  `COUNTERSIGN_TEST_POSTGRES_URL` pointing at a disposable database before
  claiming so (CI does, in `tests/integration/test_postgres.py`). Never
  point that variable at a database anyone cares about.
- **Migration `0001` is unreleased and edited in place.** A local
  `countersign.db` or a Compose `pgdata` volume older than the current
  schema is deleted and re-migrated, not patched with a new migration.
- **Ports are deliberately shifted** because other projects on the owner's
  machine hold the defaults: web `:3001`, PostgreSQL host `:5435`
  (container-internal 5432 unchanged), API `:8000`, Temporal `:7233` with
  its UI on `:8080`. Do not "normalize" them back, and do not disturb other
  development servers running on this machine.
- **`classify_authority` is deliberately conservative.** Only known ATS
  hosts (Greenhouse, Lever, Ashby, SmartRecruiters) and explicitly mapped
  company hosts reach `OFFICIAL_*`; verified `OPEN` still requires an
  accepting apply entry. An unmapped company-controlled page stays at
  most `OPEN_THIRD_PARTY_ONLY`. Do not add host or path heuristics.
- **The page reader is the only model-driven navigation** (ADR-012). Its
  moves are `RENDER` and `FOLLOW`, both read-only and allowlisted by
  construction; its claims count only after source/citation checks,
  including the observed control-label/target pair;
  its budgets live in `ReaderConfig`, never in the prompt. The one
  code-initiated render is the recorded step-0 pre-render of a shell page
  (no visible HTML text and no controls). Render network policy is
  ADR-015: the main document may land only on the page host and its
  subdomains; resources may load from hosts the fetched HTML itself names,
  DNS-pinned, GET/HEAD, capped. No registrable-domain guesses and no
  vendor or CDN lists. Do not add a move, widen either allowlist, or let a
  model claim bypass the check without a new ADR.
- **No optional nested objects at decision points in model-output
  contracts.** `ApplyEntry | None` let live models narrate an Apply control
  in `notes` and leave the field null on the same page, across routes and
  models; a required object with an explicit `NONE` kind fixed it. Model
  reasons (`why`, `notes`) are persisted; diagnose an empty reading with
  `python -m countersign.diagnostics.replay_reading <case or snapshot id>`
  before touching the prompt.
- **Rounds share one workflow id** (`job-case/{case_id}`, ADR-014) through
  Continue-As-New; reading, verification and approval ids include the
  generation via `domain/ids.py`. Each run accepts only its
  own round's approval Signal from its first event. "In flight" is
  computed by the server from stored records (`repo.in_flight`); do not
  reintroduce a client-side state list.
- **Official hosts are explicit operator policy** (ticket 002): exact
  normalized hostnames from the file named by
  `COUNTERSIGN_OFFICIAL_HOSTS_PATH`, nothing else. Do not add wildcards,
  registrable-domain guesses, path rules, company-name matching or
  model-authored entries; vendor ATS hosts belong in the code list, and a
  board stays a board. Consumers take an observation's authority from the
  reading that classified it, falling back to the snapshot stamp only for
  readings written before it was recorded. `replay_reading` never reads
  the worker's mapping; it takes `--policy`.
- **The approval gate is server-owned** (ADR-013, amended 2026-09-27).
  `APPROVE` is offered on any verification pinned to the request that is
  not `CLOSED`/`EXPIRED`; approving a posting the system did not verify
  `OPEN` is recorded by the decision Activity as the reviewer's own
  verification (row flag plus the `human.verification_attested` audit
  event), derived from the pinned verification, never typed. Do not add a
  checkbox, note or decision-time recompute back, do not widen or narrow
  the stored set in the UI, and do not infer an apply entry in code. The
  model's `APPLY_NOW` framing stays gated on `OPEN` (`gated_decision`).
- **Import boundaries are enforced by import-linter**: `contracts` imports
  nothing internal; `workflows` imports only `contracts` and `domain`;
  `domain` imports only `contracts`. Workflow code reaches Activities only
  through `workflow.unsafe.imports_passed_through()`. I/O lives in
  Activities.
- **Never push.** Commit only when asked. Docs and code go in separate
  commits when asked.
- **Owner-reserved decisions**: edits to v4 and to accepted ADRs
  (assessment first, then "write it in"); the claim level and any public
  wording about the project (§25); rubric and policy values (they live in
  `config/operator/`, never in tracked files); model provider and route
  defaults; flipping a slice to done; anything that widens the `MODEL`
  invocation class (ADR-011). Executing an already-adopted decision needs
  no re-confirmation.
- **Verification is reported as run**: name the command, the database it
  ran against, and the actual counts; if something was skipped, say so. The
  local commands are the CI ones: `ruff check .`, `ruff format --check .`,
  `pyright`, `lint-imports`, `pytest`, `pnpm --dir apps/web typecheck`,
  `pnpm --dir apps/web build`. A layered commit series is verified per
  commit (fresh worktree and venv at each point), the standard Slice 1 set.

## Code changes

- Keep changes surgical: every changed line traces to the request; match
  existing style; remove only what your change orphaned; mention unrelated
  dead code rather than deleting it.
- Prefer the simplest form that does the job; no abstractions for
  single-use code, no speculative configurability. Special cases layered on
  shared infrastructure signal a fix at the wrong depth.
- Turn tasks into verifiable goals (a reproducing test before a fix). A
  component that cannot name the domain constraint it serves fails v4 §2
  and is not added.
