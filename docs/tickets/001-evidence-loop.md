# Ticket 001 — Make REQUEST_EVIDENCE re-evaluate with the reviewer's context

- ADR: [ADR-014](../adr/014-evidence-loop-through-request-evidence.md)
  (accepted 2026-09-25: continue-as-new, §20 edit, evaluator wording)
- Status: Ready for implementation
- Slice: closes the Slice 1 state machine's own edge; scheduled before the
  Slice 2 watchlist work
- Estimate: about one working day including tests

## Goal

A `REQUEST_EVIDENCE` decision with a reason leads to a new generation of
the case: the posting is re-observed, the evaluation runs again with the
reason supplied as operator context, and a new approval request appears.
Today the decision ends the case.

## Out of scope

Chat with the model; a notes table; reader navigation driven by the
reviewer's note; résumé or cover-letter drafting; any change to
authority classification or the approval gate's status rules.

## Changes

Runtime and ids
- `runtime.workflow_id_for(case_id)` returns `job-case/{case_id}`; the
  generation segment and parameter go. Deployment: decide or let expire
  every pending approval first (already-started workflows keep the old
  id; a Signal to the new id would not reach them), then deploy. No
  compatibility shim.
- New pure module `domain/ids.py` with `approval_request_id(case_id,
  generation)` and `verification_id(case_id, generation)`; `repo`,
  `case_activities` and the workflow use it instead of inline f-strings.

Contracts (`src/countersign/contracts/`)
- `cases.py`: `ApprovalRequestView.generation: int`;
  `OperatorContext(decision_id, supplied_at, text)`;
  `CaseDetail.operator_context: list[OperatorContext]`;
  `CaseDetail.in_flight: bool`.
- `evaluation.py`: `EvaluationRecord.operator_context_ids: list[str] = []`.
- `workflow_io.py`: `CaseWorkflowStatus.generation: int`.

Domain (`src/countersign/domain/`)
- `gates.py`: `MAX_GENERATIONS = 3`;
  `allowed_decisions_for(verification, attestation=None, *, generation=1)`
  omits `REQUEST_EVIDENCE` when `generation >= MAX_GENERATIONS`.

Workflow (`src/countersign/workflows/job_case.py`)
- `run()` sets `_approval_request_id = approval_request_id(case_id,
  generation)` before its first Activity; `submit_decision` drops any
  Signal for another id from the first event of every run (today it
  accepts anything while the id is unset).
- Generation ≥ 2 skips `snapshot_source` and `normalize_case`.
- After `finalize_decision`, when the decision is `REQUEST_EVIDENCE` and
  `generation < MAX_GENERATIONS`: `workflow.continue_as_new` with
  `generation + 1`; otherwise return as today.
- The status query reports the generation.

Persistence (`src/countersign/persistence/repo.py`)
- `operator_context(session, case_id) -> list[OperatorContext]`: reasons of
  `REQUEST_EVIDENCE` decisions on the case's approval requests, oldest
  first.
- `in_flight(session, case_id) -> bool`: the pipeline states, or
  `NEEDS_MORE_EVIDENCE` with the latest approval decided
  `REQUEST_EVIDENCE`, its generation below the cap, and no verification
  row for the next generation. No migration.

Activities (`src/countersign/activities/case_activities.py`)
- `evaluate_case`: load `operator_context`, pass it to `run_evaluation`,
  record the ids. `create_approval`: pass `generation` to the gate.

Gateway (`src/countersign/gateway/evaluator.py`)
- `run_evaluation(..., operator_context=None)`: delimited block in the
  user prompt after the posting text, each note prefixed with its
  supplied-at date. In the system prompt, the two rules "judge fit only
  against the supplied profile" and "without a profile every fit judgment
  is conditional" are replaced by the paragraph quoted in ADR-014 §3
  (owner's wording). `PROMPT_VERSION = "evaluator/0.3.0"`; policy
  version and the reader prompt unchanged.

API (`src/countersign/api/routes.py`)
- `get_case` returns `operator_context` and `in_flight`;
  `submit_decision` recomputes the gate with `request.generation`.

Web (`apps/web`)
- `lib/api.ts`: the new fields. Polling uses `detail.in_flight`; the
  `IN_FLIGHT_STATES` set goes. A `useEffect` keyed on
  `approval.approval_request_id` clears the reason, the attestation
  checkbox and its note when a new approval request appears.
- Case page: the Approval header shows the round ("Approval · PENDING ·
  round 2 of 3"); the `REQUEST_EVIDENCE` button reads "Add context and
  re-evaluate" with helper text under the reason field: "Your note is
  recorded as your decision. The posting is read again and evaluated
  again with your note as operator-supplied context. Facts about you in
  general belong in your profile."; when the server omits the decision at
  the cap, a line says the re-evaluation rounds are used; a "Context you
  supplied" list shows prior notes with dates.

## Tests

- `tests/unit/test_verification_domain.py`: the cap removes
  `REQUEST_EVIDENCE` at generation 3 and only there.
- `tests/unit/test_gateway.py`: with context, the user prompt carries the
  delimited block and the record lists the ids; without, neither appears;
  the system prompt carries the attribution rule; prompt version bumped.
- `tests/unit/test_repo.py`: `operator_context` returns only
  `REQUEST_EVIDENCE` reasons, oldest first, across generations.
- `tests/workflow/test_job_case_workflow.py` (harness): a
  `REQUEST_EVIDENCE` Signal leads to a second run that skips snapshot and
  normalize and reaches `PENDING_APPROVAL` with generation 2; the status
  query follows the run chain. **Late cross-round Signal:** a duplicate of
  the round-1 Signal delivered while round 2 has not yet created its
  approval is ignored, and round 2 still waits for its own decision.
- `tests/unit/test_runtime.py` (or wherever `workflow_id_for` is covered):
  the id is `job-case/{case_id}` with no generation segment.
- `tests/workflow/test_end_to_end_recovery.py`: real Activities: request
  evidence with a note, then approve in round 2. Exactly two
  verifications, two evaluations, two approval requests, two decisions;
  the round-2 evaluation record lists the round-1 decision id; audit
  actions on the case remain unique per applied transition (the
  `NEEDS_MORE_EVIDENCE → OFFICIAL_SOURCE_VERIFIED` re-entry audits).
- `tests/integration/test_api_routes.py`: `get_case` exposes the context;
  the gate at the cap returns 400 for `REQUEST_EVIDENCE`; `in_flight` is
  true for a case in `NEEDS_MORE_EVIDENCE` after a `REQUEST_EVIDENCE`
  decision and false once a verification for the next generation exists.
- Semantic regression (live, recorded in `HANDOFF.md`, not in CI): a
  posting without salary plus a note that states one must leave
  `salary_extraction` empty and the compensation signal `UNKNOWN`. If it
  leaks, the follow-up is a code check that the extracted amount occurs
  in the posting text.

## Verification to report

The standard set: `ruff check .`, `ruff format --check .`, `pyright`,
`lint-imports`, `pytest` on SQLite and on a disposable PostgreSQL with
`COUNTERSIGN_CHROMIUM_PATH` set, `npm run typecheck` and `npm run build`
in `apps/web`. Then, after the pending approvals on the running stack are
decided or expired, one live round on the rebuilt stack: a case that ends
UNVERIFIED, one `REQUEST_EVIDENCE` with a candidate-side note, the case
page still refreshing through the re-round, and the round-2 evaluation
visibly attributing the note; plus the salary regression above. No
migration; rebuild the image only.

## Risks

- The reviewer writes a posting fact into the note ("the role is remote")
  and the model takes it on faith. Mitigation: the labelled block and the
  attribution rule; status and salary source stay code-decided. Revisit
  with a code check if it shows up in live use.
- A re-verification that finds the posting closed or empty ends the case
  without a new approval, as the existing paths do; the UI already shows
  those states.
- Owner-approved design is recorded in v4 §20: `job-case/{case_id}`,
  generation in workflow inputs and in reading, verification, evaluation,
  and approval-request identities. The evaluator wording is settled in
  ADR-014 §3.
