# Ticket 004 — Scheduled watch: discover on watched boards, recheck pending cases

- **Status:** closed 2026-10-29 after its two-week live acceptance. Code
  `a15cc23`, live since 2026-10-14. Two review findings and four paths not
  yet observed live carry over as follow-ups (below).
- **Basis:** [ADR-016](../adr/016-scheduled-watch.md), with
  [ADR-001](../adr/001-temporal-durable-workflow.md),
  [ADR-005](../adr/005-official-source-authority.md),
  [ADR-012](../adr/012-bounded-page-reader.md),
  [ADR-013](../adr/013-human-attestation-for-unverified-postings.md) and
  [ticket 003](003-ashby-adapter.md); v4 §§8.3, 9–12; Slice 2.

## Summary

Postings go stale while they wait for review, and they seldom say so
with a 404. A revisit of eleven postings two to three weeks old found
five gone, none by 404 or 410: notices on pages that still answered 200,
redirects to listings, a passed deadline. A taken-down Ashby posting
leaves a static shell with no text; only the rendered page says "Page
not found". The watch is the system's first autonomous actor: on a fixed
calendar it reads the operator's chosen ATS boards through their public
APIs, sends new postings through the normal pipeline, and rechecks the
cases waiting for a decision, closing those whose source says the
posting is gone. A human still decides every application. The watch
reads no board without a clean API (v4 §11).

## What was built

- **Watchlist.** A private JSON policy file (synthetic example in
  `config/watchlist.example.json`): calendar, Ashby and Greenhouse boards,
  region substrings, title exclusions, a lookback window (14 days by
  default) and two per-run budgets, validated and fingerprinted at worker
  start. No title keywords select postings: relevance is the evaluator's
  job, so a filter may cost an evaluation but never silently drops a
  relevant posting. A posting without location text passes and is counted.
- **Schedule.** One Temporal Schedule built from the file, skipping
  overlaps, with a one-day catch-up window, paused when the file is unset.
- **Discovery.** One request per board to its public API. A posting that
  passes the filters, is not covered by a case in any state and was
  published or republished inside the window becomes a case, newest
  first, up to `max_new_cases_per_run`. The window is fixed, so a posting
  one run could not admit stays a candidate until it ages out. Case ids
  derive from the posting, so a retried or replayed run creates nothing
  twice; each case runs the full pipeline in its own workflow.
- **Rechecks.** Cases whose approval is still pending, oldest
  verification first, rerun the existing verify path and record a new
  reading and verification without changing state. The model is skipped
  when the text is unchanged and the previous reading's cited claims still
  hold; the decision table then runs at the current time, so a passed
  deadline still yields EXPIRED. The approval keeps the verification it
  was issued for; the case page shows the latest. Approved cases are not
  rechecked.
- **Closure.** A recheck yielding CLOSED or EXPIRED closes the case, and
  so does absence from a healthy Ashby board, with no JobPosting on the
  page, on two consecutive rechecks. A `source_closed` Signal competes
  with the reviewer's decision for one outcome; it expires the approval
  with the reason and moves the case to CLOSED_OR_STALE. Nothing reopens:
  a closed posting published again is recorded once as `case.reappeared`.
- **Budgets.** Each admitted case is charged its worst case of eight model
  calls. A recheck starts only while its worst case of six fits, and the
  calls it did not make are credited back, so neither side starves the
  other. Temporal retries after outages fall outside the budget.
- **Trail.** One `watch.run` audit event per run (watchlist identity,
  board outcomes, counts, charges), `case.created` with actor `watch` and
  the usual transition events. No schema change.

Out of scope, and since taken up by
[ticket 005](005-profile-driven-discovery.md): screening new postings with
a model, and finding postings beyond the watched boards. Still out of
scope: rechecking approved cases, dedupe on paste, reposts under a new id,
reopening, other ATS adapters, digests and a watchlist UI.

## Verification

- **Code `a15cc23`**, fresh worktree and venv (Python 3.14.3): ruff,
  pyright and import-linter passed; `pytest -ra` **348 passed, no
  skips** on a disposable PostgreSQL database; web typecheck and build
  passed. Board and page responses and model providers were fakes.
  Review: nine findings; three fixed, six accepted (the notable ones are
  below). The owner's review found that a retried recheck could credit
  back calls its first attempt had made; fixed with a reproducing test.
- **Activation, 2026-10-14.** Creating, pausing, resuming and updating the
  Schedule were checked on the real server. Four manual runs created 5,
  5, 4 and 0 cases; all 14 reached an official OPEN, an evaluation and
  PENDING_APPROVAL, and with two earlier cases they covered the 16
  matching postings once each. The fourth run rechecked 22 cases, 20
  without a model call.
- **First closures, 2026-10-19.** Two takedowns closed on the first
  recheck after them: the rendered page read "page not found", the reader
  cited it as a closed notice, and the approvals expired with the
  source-closure reason. The board-absence rule was not needed.
- **Source expansion, 2026-10-26.** One Ashby and one Greenhouse board
  were added for postings inside the window; one manual run read the three
  boards and created four cases within budget.
- **Two-week acceptance, 2026-10-29.** Six calendar ticks and five manual
  runs, each with a complete `watch.run` audit: every board read returned
  HTTP 200, no recheck failed, every charge stayed within its budget, and
  the calendar kept its local hour across the daylight saving change. Of
  105 rechecks, 88 made no model call. A closed Ashby posting republished
  on 2026-10-21 was recorded once and not reopened; its `publishedAt` had
  moved, so Ashby moves it on republication, at least for this posting.
  The 2026-10-28 tick admitted two new postings unattended.

The live figures come from read-only queries and the Temporal SDK. The
two-week figures rest on the Schedule's action history and the business
audits; Temporal had already dropped the run histories.

## Remaining issues

- **Review findings.** A recheck's pending-request check and its write do
  not serialize with a concurrent decision, so a late recheck of an old
  round can become the next round's pinned verification. The Ashby
  listing drops secondary-location address fields, so a posting can fail
  the region filter despite a matching secondary address.
- **Not yet observed live:** closure by board absence, a decision
  concurrent with a closure, missed-run catch-up and overlap skipping.
- **Greenhouse.** It has no adapter in the verify path, so its postings
  close only through the decision table or approval expiry. Its window
  and `case.reappeared` use `updated_at`, which edits move too. A hosted
  posting URL can redirect to the company's own site, official only when
  mapped (ticket 002). On the one such posting observed, the job is
  embedded and the rendered page shows no body or apply control, so the
  case stays UNVERIFIED / WAIT under a generic title. That board has been
  watched only since 2026-10-26.
- **Trade-offs.** A candidate left over by the budget can age out of the
  window before the next run. A posting that moved off its URL without a
  notice closes only by approval expiry. The absence rule compares stored
  reason text, and each Ashby recheck reads the whole board. The Schedule
  lifecycle has live checks but no automated test, since Temporal's test
  server has no Schedules.

The full working record (scope, touch points, decisions, review detail,
commands and the live logs) is in git at `31f3159`.
