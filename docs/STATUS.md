# Countersign — Status

Current focus, blockers, next steps, and owner decisions. Rewritten when
these change; history lives in git and ADRs. Private live-run details stay
in the gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-09-25 |
| Branch | `main`, no remote. Evidence loop: `8f42628`; prior-assessment input and round history: `6e8b81c`. Documentation committed separately. |
| Slice | Slice 1 (one durable case): code complete; exit milestone (Track A) observed 2026-09-18; worker-kill recovery repeated 2026-09-19. |
| Current focus | Prior-assessment input and round history are committed and automatically verified. Deploy the amendment, then verify summary wording, anchoring and history navigation alongside the remaining live checks. |

## Current implementation

- **Reader (ADR-012):** bounded FOLLOW/RENDER loop inside `verify_status`;
  at most 120 controls per observation, 120 seconds after the initial
  fetch, three model steps, two follows and one schema repair per step.
  `page-reader/0.3.0` requires an explicit apply-entry choice, including
  `NONE`; reasons and notes are persisted. Replay a stored observation
  with `python -m countersign.diagnostics.replay_reading <case or snapshot id>`.
- **Rendering:** a page with no visible HTML text and no controls gets a
  recorded step-0 pre-render, including JSON-LD-only shells. Docker ships
  Chromium; host runs use `COUNTERSIGN_CHROMIUM_PATH`. Rendering is
  restricted to vetted hosts and read-only requests. Provider outages
  retry the Activity; they do not become verification verdicts.
- **Provenance:** control labels and targets are checked together; claims
  on superseded pages must hold on their replacement. Verification pins
  the governing source, which supplies the posting body for evaluation,
  salary-source stamping and case detail. Model claims do not override
  authority classification or the approval gate.
- **Approval (ADR-013):** plain APPROVE requires verified OPEN. UNVERIFIED
  and OPEN_THIRD_PARTY_ONLY can be approved with a typed human attestation
  checked against the request's pinned verification and audited. CLOSED,
  EXPIRED and missing verification cannot be attested.
- **Evidence loop (ADR-014, [ticket 001](tickets/001-evidence-loop.md)):**
  REQUEST_EVIDENCE stores the reviewer's reason and continues the same
  `job-case/{case_id}` workflow into the next generation. Later rounds
  skip intake/normalization, re-observe and re-evaluate, then create a new
  approval; three rounds maximum. Signals must match the current round.
  `evaluator/0.3.1` receives all prior REQUEST_EVIDENCE reasons as labelled
  operator context and, from round 2, the previous round's structured
  evaluation as a labelled revisable judgment (ADR-014 amendment): the
  current posting, profile and context remain the basis, overturning is
  allowed, and candidate facts the earlier round inferred are not facts.
  The previous assessment comes from the preceding approval's pinned
  evaluation, including on Activity retries. Records carry their round,
  the decision ids consumed and the previous evaluation id; records lacking
  a round recover it from the pinning approval or their historical position.
  The `summary` field states its purpose in the contract
  (decision and reasons for the reviewer; what context or the previous
  assessment resolved and what remains open; no restated rules). The
  reader receives no reviewer instructions.
- **Round presentation:** case detail returns every round's evaluation;
  the page offers ‹ › navigation (latest by default, earlier rounds
  marked superseded and shown with the context they saw; the gated
  decision applies to the latest only). It shows supplied context and
  the approval round; earlier rounds are labelled as the model's
  recommendation of that round, since the gate result of a past round is
  not stored; polling uses server-derived `in_flight`. An
  atomic `case.round.continued` marker distinguishes an active continuation
  from REQUEST_EVIDENCE decisions that ended cases before ADR-014. Intake
  replays start a workflow only for cases still in DISCOVERED.
- **Schema:** unreleased migration `0001` remains the schema baseline.
  Upgrading from before ADR-013 requires fresh schema/history; the later
  reader diagnostics and evidence loop add no migration. The workflow-id
  cutover requires no pending approvals addressed to old suffixed ids.

## Verification and deployment

Code commit `6e8b81c` was checked on 2026-09-25 in a fresh detached
worktree and Python 3.14.3 venv, with front-end dependencies installed
from `pnpm-lock.yaml`:

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  107 Python files; `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest`: **171 passed, no skips**. PostgreSQL integration used
  disposable database `countersign_assessment_code_check` at
  `127.0.0.1:49380` in a separate temporary container. Other database
  fixtures used SQLite; Temporal used its test server; rendering used real
  local Chromium. Private profile and salary inputs were unset; model
  providers were fakes. Coverage includes a retried round-2 evaluation
  retaining round 1 as its predecessor and recovery of older records'
  rounds.
- `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed.
- Changed files were checked for operator-specific identifiers,
  credential-like values and private paths; no matches. A full gitleaks
  scan remains a publication prerequisite.

**Running-stack evidence, 2026-09-25:** read-only PostgreSQL inspection of
an owner-tested case confirmed two retained verifications, evaluations
and approvals under the stable workflow id. Round 1 was decided
REQUEST_EVIDENCE; round 2 was pending. Both evaluations used
`evaluator/0.3.0`, and the second pinned the first decision as context.
The posting content was unchanged; verification remained
OPEN_THIRD_PARTY_ONLY. Exactly one continuation marker existed. No pending
approvals remained on legacy round-suffixed workflow ids. This confirms
the deployed two-round path, not browser polling or every live acceptance
check. No deployment changes or live model calls were made during this
cleanup/commit round.

## Next

1. Finish live acceptance checks: an UNVERIFIED case re-evaluated with a
   candidate-side note, polling through the round change, and a posting
   without salary plus a note containing salary (extraction must remain
   empty and compensation UNKNOWN). Broader reader/approval checks remain:
   direct ATS, board → ATS, client-rendered shell and attested approval.
2. Deploy `6e8b81c` (`evaluator/0.3.1`) and verify the round pager. Watch
   amended live summaries for what the note resolved and what remains,
   without restated rules; check more than one model for anchoring on an
   earlier inference about the candidate. Four identical-input replays
   showed score variation of ±1; small cross-round changes need caution
   until calibration cards exist.
3. Before publication, run gitleaks and inspect tracked content. The owner
   adds the remote and pushes.
4. Slice 2 retains the v4 §24 timebox: policy watchlist and official-domain
   mapping, ATS adapters, scans and stale rechecks, capability/route
   registries, calibration, traces and cost dashboard. Tickets are
   owner-read before implementation. Slice 2.5 research follows ADR-011
   once registry prerequisites exist.

## Remaining gaps and owner decisions

- Company pages still require official-domain mapping; several boards
  fall into COMPANY_CONTROLLED and TRUSTED_BOARD_REDIRECT is not assigned.
  Allowing board evidence alone to permit plain APPROVE is a policy decision.
- Cross-host script dependencies can prevent complete rendering;
  synchronous DNS/library calls are not forcibly cancelled by the reading
  budget. Citation checks establish provenance, not semantic correctness.
- Attestation is stored in the decision row and audit log, not displayed
  on the case page. Quarantine/needs-evidence state reasons remain in the
  audit log; verification reasons are displayed.
- Rubric decisions remain: no-profile score nullability, hard blockers and
  decision bands, pay-level risk wording, technical versus coordination
  work. Private operational notes remain in `HANDOFF.md`.
- Owner-reserved v4 edits: §9 needs-evidence edge; §12 / §14 old status name;
  §12 / §15 reader; §15 / §24 / §29 research loop when Slice 2.5 nears.
