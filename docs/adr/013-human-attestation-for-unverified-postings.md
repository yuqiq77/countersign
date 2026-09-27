# ADR-013: Human attestation for unverified postings

- Status: accepted
- Date: 2026-09-21
- Extends: ADR-005 (official-source authority), ADR-012 (bounded page
  reader). Renames one status label; no other ADR-005 semantics change.

## Context

The lifecycle gate (v4 §9, §13.4) admits a plain `APPROVE` only on a
verified official `OPEN`. The bounded page reader (ADR-012) leaves a
posting unverified whenever no application entry survives the citation
check on an official source: client-rendered application UIs on hosts
outside the page's own domain, company pages without an official-domain
mapping, and pages that need a login all end there. Until now the only
moves on such a case were `REJECT` and `REQUEST_EVIDENCE`, so the gate
read as "no evidence → nobody may act". ADR-005 already anticipated the
correction: any override "records the actor, reason, and exact evidence
reviewed".

The label `UNKNOWN_STALE_RISK` also overstated what the system knows. It
is assigned whenever evidence is missing, including after an HTTP 200
with a perfectly ordinary posting; "stale risk" is a possible cause, not
an observation.

## Decision

1. **Rename** `JobStatus.UNKNOWN_STALE_RISK` to `UNVERIFIED` everywhere.
   Semantics are unchanged: evidence is insufficient, conflicting, or the
   source was temporarily unavailable. The UI reads it as "Not verified —
   no application entry confirmed on an official source" and shows the
   recorded reason beside it.
2. **A reviewer may attest** that they observed the posting open
   themselves. The attestation is a typed part of the decision request
   (`observed_open: true`, a non-empty `note` describing what was seen)
   and only accompanies `APPROVE`.
3. **The gate re-checks with the attestation at decision time.** The
   allowed set stored on an approval request remains the no-attestation
   list. On submit, the API recomputes `allowed_decisions_for` against the
   verification the request pinned, never against whatever is latest.
   `APPROVE` passes when the pinned verification is `OPEN`, or when it is
   neither `CLOSED` nor `EXPIRED` and an attestation is present. A missing
   verification is not attestable; a closed or expired one never is.
4. **The attestation is persisted as a human decision fact** on the
   decision row (`attested_open`, `attestation_note`) and as its own audit
   event `human.verification_attested` (actor = the reviewer, resource =
   the approval request, reason = the note), appended in the same
   transaction as the decision. A replay that differs in attestation
   content is a conflict, not a duplicate.
5. **The web client keeps the paths visually distinct.** On an unverified
   case the plain approve button is absent; an amber attestation block
   requires the checkbox and the note before "Approve on my own
   attestation" is enabled. The decision reason stays mandatory.

## Consequences

- The gate's meaning becomes "no evidence → a human must sign for it".
  Verified `OPEN` remains the only path to an unattested approval.
- Attested approvals are countable and reviewable: the override rate and
  its notes are the labelled disagreement data v4 §17 asks for, gathered
  from the first operational use.
- Code never infers an application entry to avoid the attestation path;
  the reader's conservative failures (ADR-012) stay as they are.
- `human_decisions` gains two columns. Migration `0001` is unreleased and
  edited in place; existing databases are recreated, not patched.
- The verification enum in v4 §12 and §14 still spells the old label; that
  edit is owner-reserved. ADR-005 keeps its original text as history.

## Rejected alternatives

- **Let the reader's HTTP 200 plus posting text count as open.** Rejected:
  that is exactly the inference ADR-005 forbids.
- **A separate `OVERRIDE` decision.** Rejected: it would duplicate the
  decision lifecycle and state mapping for one flag. The attestation is
  additional evidence attached to an ordinary `APPROVE`, audited on its
  own.
- **Recompute the gate against the latest verification.** Rejected: the
  reviewer decides against the artifacts pinned to the request (v4 §10).

## Revisit when

- Scheduled rechecks (Slice 2) can turn an attested posting `CLOSED`
  after approval; the submission step must reverify regardless of the
  attestation.
- ATS adapters or an official-domain mapping make `UNVERIFIED` rare
  enough that attestation volume itself becomes a signal.

## Amendment (2026-09-27, after the Slice 1 live set)

The typed attestation asked the reviewer to prove to the system something
the system cannot use: nothing downstream consumed `attested_open` except
the audit log, and the system submits no application. Live volume also
inverted this ADR's expectation that `UNVERIFIED` would be rare: 29 of
the first 38 verifications were not `OPEN` (board postings and company
pages the authority rules cannot make official), so the attestation was
the ordinary path rather than the exception. At the round cap, a board
posting could still be approved with an attestation, but required the
extra checkbox and observation note; only REQUEST_EVIDENCE was removed.

Two gates exist in `domain/gates.py`; only one changes.

- `gated_decision`, the system's own framing, is unchanged: the model's
  `APPLY_NOW` is downgraded unless the pinned verification is `OPEN`. The
  rule "never infer open from HTTP 200" and the claim behind it (v4 §12,
  §25) stand.
- `allowed_decisions_for`, what the reviewer may do, no longer takes an
  attestation. `APPROVE` is offered whenever the request pins a
  verification that is not `CLOSED` or `EXPIRED`. Because the workflow
  ends those cases before approval and always pins a verification, every
  pending approval in practice accepts `APPROVE`; the two remaining checks
  are guards, not a path. They are not to be extended "for safety".
- A convention replaces the typed proof: **approving a posting the system
  did not verify as an official `OPEN` is the reviewer's own
  verification.** The decision Activity derives that fact from the
  verification pinned to the request, never from the Signal or the
  request body, records it on the decision row
  (`approved_without_official_open`) and appends the
  `human.verification_attested` audit event with the decision reason in
  the same transaction. ADR-005's "actor, reason, and exact evidence
  reviewed" is met by the actor, the decision reason and the request's
  pinned verification and snapshot.
- Removed: the `Attestation` contract and request field, the API's
  recompute of the allowed set and its refusal of an attestation on a
  verified posting, the Signal's attestation fields, the note column, and
  the web client's checkbox, note and separate button. The case page
  states the convention in one line under the decision buttons whenever
  the pinned verification is not `OPEN`.
- This is an incompatible development cutover: migration 0001 is edited
  in place, and both the business database and Temporal history must be
  reset before running the new image. The owner completed that cutover
  on 2026-09-27 after privately exporting the business records. Read-only
  checks confirmed the new decision column, the worker's reduced Signal
  contract and empty business/workflow stores. Old Temporal history was
  not retained; deployment confirmation is not a live approval test.

Consequence kept: approvals without an official `OPEN` remain countable
and reviewable as the labelled disagreement data of v4 §17, now without
depending on whether a reviewer ticked a box. Consequence dropped: "no
evidence → a human must sign for it". The gate's meaning is now that the
system never frames a posting as apply-now on its own, and the human's
approval carries the human's responsibility, recorded.
