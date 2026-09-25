# Ticket 001 — Make REQUEST_EVIDENCE re-evaluate with the reviewer's context

- ADR: [ADR-014](../adr/014-evidence-loop-through-request-evidence.md)
  (accepted 2026-09-25: continue-as-new, §20 edit, evaluator wording)
- Status: evidence loop in `8f42628`, ADR-014 amendment in `6e8b81c`;
  automated verification passed. The original two-round path was observed
  live; amended summaries and round history still need live verification.
- Slice: closes the Slice 1 state machine's own edge; before Slice 2
  watchlist work

## Goal and scope

A REQUEST_EVIDENCE decision leads to a new generation: the posting is
re-observed, evaluation runs again with the reason as operator context,
and a new approval appears. Previously the decision ended the case.

Out of scope: chat, a notes table, reader navigation driven by the note,
résumé/cover-letter drafting, authority classification changes and
approval-gate policy changes.

## Implementation

- **Runtime and workflow:** `job-case/{case_id}` stays stable through
  Continue-As-New; generation distinguishes reading, verification and
  approval identities via `domain/ids.py`. Later rounds skip snapshot
  intake and normalization, but re-verify and re-evaluate. Each run accepts
  only its own approval Signal from its first event. The query exposes
  generation; the gate removes REQUEST_EVIDENCE at the third round.
- **Persistence:** reasons from earlier REQUEST_EVIDENCE decisions are
  loaded oldest first. Evaluation records retain the consumed decision
  ids. Finalizing a continuing decision atomically records
  `case.round.continued`; server-derived `in_flight` uses that marker and
  the absence of the next verification during the continuation gap.
  Earlier terminal REQUEST_EVIDENCE decisions remain final. Intake replays
  start no second workflow after a case leaves DISCOVERED. No migration.
- **Evaluator:** `evaluator/0.3.1` receives a delimited operator-context
  block after the posting, with supplied-at dates, and from round 2 the
  previous round's structured evaluation as a labelled revisable judgment
  taken from the approval that pinned it (never "the latest", which on a
  retried Activity is this round's own record). ADR-014 §3 as amended
  distinguishes candidate-side context from posting evidence and asks for
  one plain acknowledgement instead of restated rules; the `summary` field
  states its purpose in the contract. Records carry their round, the
  consumed decision ids and the previous evaluation id; rounds of records
  written before the field are recovered from the pinning approval or the
  record's position. Policy identity and reader prompt are unchanged. The
  reader does not investigate the reviewer's reason; that research loop
  belongs to ADR-011.
- **API and web:** case detail exposes context and `in_flight`; approval
  exposes generation and the maximum rounds; case detail also returns
  every round's evaluation, and the current round is its last item. The
  API recomputes the gate using the request's generation. The page polls
  from the server flag, clears reason/attestation inputs when the approval
  changes, displays the round and prior notes, and labels REQUEST_EVIDENCE
  “Add context and re-evaluate”. At the cap it explains that rounds are
  used up. Earlier rounds are paged with ‹ › and shown as the model's
  recommendation of that round, with a note that the round's gate result
  is not shown; the gated decision applies to the latest round only.

The stable-id deployment cutover required deciding or expiring old
pending approvals first; no compatibility shim was added. Read-only
inspection on 2026-09-25 found zero pending approvals on legacy suffixed
workflow ids.

## Verification

Fresh detached worktree at `6e8b81c`, fresh Python 3.14.3 venv and locked
front-end dependencies, 2026-09-25:

- `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`: passed
  (107 Python files); `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest`: **171 passed, no skips**, including disposable
  PostgreSQL database `countersign_assessment_code_check` on `127.0.0.1:49380`,
  SQLite fixtures, Temporal test server and real local Chromium.
  Private profile/salary environment inputs were unset; model providers
  were fakes.
- `pnpm --dir apps/web typecheck`, `pnpm --dir apps/web build`: passed.

Coverage includes the cap, prompt attribution/context ids, ordered notes,
API fields, continuation-aware polling, old final decisions and intake
replays. The workflow test holds round 2 inside its first Activity while
both a duplicate round-1 decision and a late round-1 APPROVE are delivered;
neither resolves round 2. The real-Activity test requests evidence then
approves in round 2, retaining both rounds and one continuation marker.
A repeated round-2 evaluation still references the first round's pinned
assessment. Repository tests recover rounds from records written before
the field existed; gateway tests cover the labelled previous assessment
and the summary contract.

Read-only inspection of an owner-tested live case confirmed two retained
rounds under the stable workflow id, both with `evaluator/0.3.0`. Round 2
pinned the first decision as context and awaited approval; the posting
was unchanged and verification remained OPEN_THIRD_PARTY_ONLY. Private
case details stay in `HANDOFF.md`. No live model calls or deployment
changes were made during the cleanup/commit verification.

## Remaining acceptance checks and limits

- Live UNVERIFIED case, candidate-side note, browser polling through the
  round change and visible attribution of the note remain to verify.
- Semantic salary regression: a posting without salary plus a note
  containing salary must leave extraction empty and compensation UNKNOWN.
  If leakage is observed, revisit a code check against posting text.
- Attribution is a model rule, not proof of semantic correctness. Status
  and salary source remain code-owned.
- A re-verification that finds a closed or empty posting can end the case
  without another approval, using the existing terminal paths.
- The previous-assessment input and round pager are implemented. Live
  checks of history navigation, amended summary wording and anchoring on
  an earlier model inference remain; automated checks use fake providers.
