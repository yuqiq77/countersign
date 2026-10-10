# Ticket 004 — Scheduled watch: discover, deduplicate, recheck

- Status: draft for owner review; not approved for implementation.
- Slice: Slice 2 ("scheduled scans and stale rechecks", v4 §24). The
  first autonomous actor in the system: it creates cases and closes
  stale ones without a human in the loop; humans still approve every
  application.
- Basis: [v4 §8.3, §9, §10, §11 item 2, §12](../job-search-agent-architecture-v4.md),
  [ADR-001](../adr/001-temporal-durable-workflow.md),
  [ADR-005](../adr/005-official-source-authority.md),
  [ticket 003](003-ashby-adapter.md) (the only discovery source in v1).
  Whether autonomous creation and closure need their own ADR is the
  owner's call before implementation.

## Why

Postings on the operator's market come down within days. Today a case
exists only when the operator pastes a URL, and nothing looks at it
again after approval; a stale case is noticed by the operator or by the
7-day approval expiry. v1 closes the loop with what exists: the Ashby
posting API lists a board's published postings in one GET, the case
workflow already handles CLOSED/EXPIRED and expiry, and Temporal
Schedules are the only background system (v4 §10).

## Scope

1. **Watchlist, a private policy file** (like the official-host
   mapping): `COUNTERSIGN_WATCHLIST_PATH`, JSON `{version, boards:
   [{adapter: "ashby", board}], title_keywords: [...], location_keywords:
   [...], max_new_cases_per_run, max_rechecks_per_run,
   recheck_after_hours}`. Keywords are case-insensitive substrings; a
   posting passes when at least one title keyword matches and, when
   location keywords are given, at least one matches its location or the
   posting is remote. Validated at worker start, 8-hex fingerprint, unset
   means no watch. Synthetic example in `config/watchlist.example.json`.
2. **One Temporal Schedule**, `countersign-watch`, interval from
   `COUNTERSIGN_WATCH_EVERY` (e.g. `6h`), overlap policy skip, ensured
   idempotently by the worker at startup when both variables are set and
   paused when the watchlist is unset. Manual runs through the Temporal
   UI or CLI; no second scheduler.
3. **`WatchWorkflow`, one run per tick, two Activities.**
   - `discover_postings`: per board one API GET through the ticket-003
     adapter (a `list_board` function beside `lookup`), filter by the
     watchlist, phase-1 deduplicate (v4 §8.3) by `(adapter, board, job
     id)` — realized as a case whose URL starts with the canonical
     `jobUrl`, since `job_cases` has no source-key column and this ticket
     adds none — then create at most `max_new_cases_per_run` new cases
     (newest `publishedAt` first) in DISCOVERED with actor `watch` and the
     schedule run as request id. The workflow then starts each case's
     `JobIntelligenceWorkflow` as an abandoned child with the same id and
     reuse policy the API uses, so a replayed tick starts nothing twice.
     Every case runs the full pipeline, evaluator included: the cap is
     the cost guard, at most that many model evaluations per tick.
   - `recheck_cases`: candidates are pre-submission cases
     (PENDING_APPROVAL, APPROVED) whose latest verification is older than
     `recheck_after_hours`, oldest first, at most `max_rechecks_per_run`.
     Each recheck runs the verify path as today (adapter first, reader
     otherwise) under recheck ids `ver-{case}-g{n}-r{k}` /
     `read-{case}-g{n}-r{k}`, so the approval's pinned verification stays
     untouched and case detail shows the latest. Ashby rechecks need no
     model; reader rechecks spend one reading.
4. **Closure rule (ADR-005).** A recheck closes a case when the source
   says so — CLOSED/EXPIRED from the decision table — or when the posting
   is absent from a healthy board (HTTP 200 with postings) on two
   consecutive rechecks; a single absence is recorded as UNVERIFIED with
   the reason and waits for the next tick. Closing a PENDING_APPROVAL
   case goes through its running workflow: a new Signal `source_closed`
   ends the approval wait and the existing expiry Activity records
   "source closed on recheck: …", expires the approval and moves the case
   to CLOSED_OR_STALE. Closing an APPROVED case (its workflow has ended)
   is a direct transition by the Activity, which needs `APPROVED →
   CLOSED_OR_STALE` added to the state machine; v4 §9's rule text
   ("pre-submission cases") allows it, the diagram does not show it —
   owner's decision. Nothing reopens a closed case in v1.
5. **Trail.** Per tick one audit event `watch.run` with counts (boards
   read, postings seen, passed the filter, already known, created,
   rechecked, closed); per case the existing `case.created` (actor
   `watch`), the recheck's verification and reading records, and the
   usual state-transition events with reasons. The case page needs no
   change beyond showing the latest verification, which it already does.

## Out of scope

Discovery from any other source (other ATS APIs, feeds, pasted
reports); phase-2 canonical merge across sources; re-evaluation when the
posting's content hash changes (`content_changed` is recorded, not acted
on); reminders and digest exports; reopening closed cases; a
`source_key` column (next schema reset); a watchlist UI; rechecking
cases after SUBMITTED.

## Touch points

- `policy/watchlist.py` + contract; `capabilities/ats/ashby.py`:
  `list_board`; `domain/states.py`: the one new transition;
  `domain/ids.py`: recheck ids; `repo`: case-by-URL-prefix lookup,
  recheck candidates, `watch.run` audit; `activities/watch_activities.py`;
  `workflows/watch.py`; `workflows/job_case.py`: the `source_closed`
  Signal in the approval wait; `runtime`/`worker`: schedule ensure;
  Compose and `.env.example`.
- Tests: unit (watchlist validation, filters, dedupe by URL prefix,
  closure counter); workflow (a tick creates capped cases, skips known
  ones and starts children once; a recheck closes a waiting case through
  the Signal and an approved case directly; one absence waits, two
  close; a replayed tick creates nothing twice); PostgreSQL (prefix
  lookup); contract (old records unchanged).

## Acceptance and verification

- With a watchlist naming one board and keywords matching a few of its
  postings: one tick creates at most the cap, each through the normal
  pipeline to PENDING_APPROVAL; a second tick creates nothing new.
- A posting removed from the board: first recheck records UNVERIFIED
  with the reason, second closes the case with the audit trail; a
  posting whose page returns 410 closes on the first.
- A pasted case and a discovered case for the same posting are one case.
- Run and report the usual checks with counts and the disposable
  database name.
- Live: one board watched for several days on the owner's deployment;
  at least one discovered case reaching approval and one takedown
  observed as a closure. Identifiers stay in private notes.

## Completion record

Not implemented.
