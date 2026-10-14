# Ticket 001 — Make REQUEST_EVIDENCE re-evaluate with the reviewer's context

- **Status:** closed. Evidence loop in `8f42628`; previous-assessment
  context and round history in `6e8b81c`.
- **Basis:** [ADR-014](../adr/014-evidence-loop-through-request-evidence.md),
  accepted 2026-09-25 and amended with the second commit.

## Summary

A REQUEST_EVIDENCE decision used to end the case. Now the reviewer's
reason becomes operator context for a new round: the same workflow
re-verifies the posting, evaluates it again with the note, and opens a new
approval, up to three rounds. From round 2 the evaluator also sees the
previous round's assessment as a judgment it may revise.

## What was built

- **Workflow.** One stable workflow id, `job-case/{case_id}`, across rounds
  through Continue-As-New; the round number is part of every reading,
  verification and approval id. Later rounds skip intake but re-verify and
  re-evaluate. Each run accepts only its own round's approval Signal, and
  REQUEST_EVIDENCE is no longer offered in round three.
- **Records.** Earlier reasons load oldest first. Each evaluation records
  its round, the decisions it consumed and the previous evaluation; a
  continuing decision writes `case.round.continued` in the same
  transaction, and the server derives "in flight" from these records.
  Older records recover their round. No migration.
- **Evaluator.** The note arrives in a delimited, dated operator-context
  block after the posting. The previous assessment comes from the approval
  that pinned it, never from "the latest evaluation", which a retried
  Activity would make this round's own. The reader does not investigate
  the note; that belongs to [ADR-011](../adr/011-bounded-company-research-loop.md).
- **API and web.** Case detail returns the context, the in-flight flag and
  every round's evaluation. The page polls from the server flag, labels the
  action "Add context and re-evaluate", says when rounds are used up, and
  pages through earlier rounds as that round's model recommendation.
- **Cutover.** Stable workflow ids required deciding or expiring old
  pending approvals first; none were pending, so no compatibility shim was
  added.

## Verification

- **Commit `6e8b81c`**, fresh worktree and venv (Python 3.14.3): ruff,
  pyright and import-linter passed; `pytest` **171 passed, no skips** with
  a disposable PostgreSQL database, Temporal's test server and local
  Chromium; web typecheck and build passed. Model providers were fakes.
  A held round 2 ignores both a duplicate and a late round-1 APPROVE, and
  the real-Activity test keeps both rounds and one continuation marker.
- **Live, 2026-09-25 and 2026-09-26:** two rounds retained under the
  stable workflow id, round 2 pinning the first decision as context. Later
  runs covered the round pager and polling across three rounds, a second
  round that pinned the preceding evaluation and answered the reviewer's
  question, and salary mentioned only in a note staying out of extraction.

## Remaining issues

- Attribution of the note is a model rule, not a semantic guarantee;
  status and salary source stay code-owned.
- A re-verification that finds the posting closed or empty ends the case
  through the existing terminal paths, without another approval.
- Anchoring on an earlier model inference has not been checked live.

The full working record is in git at `226b058`.
