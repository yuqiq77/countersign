# ADR-016: Scheduled watch — the first autonomous actor

- Status: accepted (2026-10-11, with ticket 004)
- Date: 2026-10-11
- Extends: ADR-001 (Temporal is the only background system), ADR-005
  (official-source authority, closure evidence), ADR-011 (no widening of
  the MODEL class), ADR-012 (the reader), ticket 003 (ATS API as an
  official observation). Implemented by [ticket 004](../tickets/004-scheduled-watch.md).

## Context

Until now every case began with a human pasting a URL, and nothing looked
at a case again until its approval expired after seven days. Postings
come down within days. A revisit of eleven earlier postings found five
gone, none by HTTP 404/410: careers sites answer 200 with a "job not
found" notice, boards redirect to a listing, an ATS shows "expired". An
API-backed posting that was taken down answers 200 with a client-side
shell that has no JobPosting and no text, its board API no longer lists
it, and the rendered page reads "Page not found".

Broad discovery across the market is not this system's job (v4 §11:
sources without an API are not scraped). Its part is narrower and
governed: read the operator's chosen ATS boards through their public
APIs at a fixed cadence, put new postings through the same pipeline a
pasted URL gets, and recheck what waits for review. Humans still approve
every application; nothing here applies.

Both ATS vendors let a posting be unpublished and published again; one
supports timed unpublish-and-republish cycles. A posting can therefore
leave a board and return under the same id.

## Decision

1. **One Temporal Schedule drives one `WatchWorkflow`.** Its cadence,
   boards, filters and budgets come from a private, versioned watchlist
   file (`COUNTERSIGN_WATCHLIST_PATH`, content fingerprint recorded on
   every run), validated at worker start; the worker creates or updates
   the Schedule and pauses it when the file is unset. There is no second
   scheduler and no queue outside Temporal.
2. **Discovery is stateless; the board is the queue.** Each run lists
   every watched board, drops postings a case already covers, keeps those
   published or republished within a fixed lookback window (the first
   run backfills it; later runs see new postings and whatever earlier
   runs could not admit), and admits the newest first up to the run's
   discovery budget. The window and the order use the vendor's latest
   publication timestamp (Ashby `publishedAt`; Greenhouse `updated_at`,
   since `first_published` never moves on republication), so an edit can
   bring an older posting back into the window at the cost of one
   evaluation at most, after which it is known.
   A case's id derives from the posting (adapter, board, job id), so a
   retried Activity, a replayed run or two overlapping runs cannot create
   it twice; the case workflow starts with the intake's id and reuse
   policy. Filters are structured facts only (location texts, a title
   exclusion list); the watch never judges relevance, which stays the
   evaluator's job inside the governed pipeline. A filter may fail by
   costing an evaluation, never by dropping a relevant posting silently;
   a posting without location text passes and is counted. A posting
   whose case ended by source closure and which the board lists again,
   published after that closure, is recorded as reappeared before any
   window test, whatever the gap: one audit event per closed case, since
   Greenhouse's `updated_at` also moves on edits and a timestamp key
   would count each edit as another republication. It is not admitted
   again: the operator re-pastes it for a fresh case.
3. **Rechecks touch only cases waiting for a decision.** A pending case
   whose latest verification predates the run is verified again by the
   unchanged verify path under run-derived ids; the record is written
   only if the case still waits on the same approval request, and the
   approval keeps the verification pinned at its creation. The recheck
   skips the model only when two existing checks both pass on the fresh
   observation: the normalized text hash is unchanged, and the previous
   reading's verified claims still bind under the reader's own citation
   check (same label, same target). Then the decision table runs again
   at the current time, so a deadline that has passed since still yields
   EXPIRED; a changed apply target or a new notice fails a check and the
   reader runs. A target that still reads the same but no longer works
   is not detected, as it was not at first verification. Approved cases
   are not rechecked until a submission can be recorded.
4. **Closure needs evidence the decision table already accepts, plus one
   deterministic signal.** A recheck closes a pending case when the verify
   path returns CLOSED or EXPIRED (HTTP 404/410, a cited closed notice, a
   past `validThrough`), or when an API-backed posting is absent from a
   healthy board (HTTP 200 with postings) **and** its page shows no
   JobPosting, on two consecutive rechecks of the case — ADR-005's
   "second observation". API-backed means the verify path reads the
   posting's board (Ashby, ticket 003); consecutive counts the case's
   own verifications, and any recheck that does not observe the absence
   breaks the chain.
   A closure is a Signal to the case's running workflow carrying the
   approval request id; it shares one outcome slot with the decision
   Signal, first wins, and runs the existing expiry Activity: the approval
   becomes EXPIRED with the reason "source closed on recheck: …", the case
   CLOSED_OR_STALE, the decision API answers 410. Nothing reopens a case.
5. **Two budgets, so neither side starves the other.** Discovery admits at
   most `max_new_cases_per_run` cases (each charged its worst case of
   eight model calls, spent later in its own workflow); rechecks spend at
   most `max_recheck_model_calls_per_run`: a recheck starts only while
   the remaining budget covers its worst case of six, is charged six on
   admission and credited back what it did not use when it returns (0 on
   the API path or an unchanged hash), oldest first, the rest waiting for
   the next run. The budget is never exceeded. Not covered: Temporal
   retries after outages and evidence rounds the operator starts. Actual
   usage stays on every reading and evaluation.
6. **Trail.** One `watch.run` audit event per run with the watchlist
   identity and counts (boards and their HTTP outcome; postings seen,
   filtered, known, created; cases rechecked, unchanged, closed; budget
   spent); `case.created` with actor `watch`; recheck readings and
   verifications; the usual transition events with reasons.

## Consequences

- The system acts on its own twice: it creates cases and it closes
  pending ones. Both are bounded by the file's budgets, derive from
  observed facts, and leave the same records a human-started case does.
  The human gate is unchanged.
- The first run backfills the window; its cost is the window times the
  board's rate of relevant postings, bounded per run.
- Reader-path rechecks are the running cost; the hash short-circuit
  removes it while a page is unchanged.
- A URL pasted after the watch created its case makes a second case
  (bounded cost, deliberate re-pastes keep working); a posting that moved
  off its URL without a notice stays UNVERIFIED and closes by expiry.
- Known, accepted: a candidate not admitted under the budget can leave
  the lookback window before the next run; a republished posting is
  recorded, not reopened.
- No new model invocation class: the watch calls no model itself.

## Alternatives considered

- **A persistent candidate queue** for postings not admitted under the
  budget. Rejected: the board listing plus the lookback window and the
  known-case check carry candidates over within the window with no state
  to repair; a candidate that leaves the window unadmitted is the
  accepted trade-off.
- **One shared budget, rechecks first.** Rejected: a run after a large
  backfill can spend everything on rechecks and admit nothing.
- **Closure only through the decision table.** Rejected for API-backed
  postings: their taken-down page is a shell with no notice, so the
  reader mostly cannot close it, while the board's absence is observed
  directly and cheaply.
- **LLM triage of candidates before intake.** Rejected: it widens the
  MODEL class (ADR-011) for a judgment the evaluator already makes.
- **Rechecking approved cases.** Deferred until a submission can be
  recorded; until then an approved case would be rechecked until its
  posting closes.

## Revisit when

- A submission state exists (recheck approved cases until then).
- The scan-report import adapter (v4 §11 item 5) brings sources without
  an API into the same loop.
- A deterministic rule for postings that moved off their URL without a
  notice is wanted, or a reader evidence kind for it (ADR-012 amendment).
- A second reviewer exists, or notifications are wanted.
