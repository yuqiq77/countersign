# Ticket 004 — Scheduled watch: discover on watched boards, recheck pending cases

- Status: approved 2026-10-11 with ADR-016 accepted.
- Slice: Slice 2 ("scheduled scans and stale rechecks", v4 §24). The
  first autonomous actor in the system: it creates cases, and it closes
  pending cases whose source says the posting is gone, without a human
  in the loop; humans still approve every application.
- Basis: [v4 §8.3, §9, §10, §11 items 2–3, §12](../job-search-agent-architecture-v4.md),
  [ADR-001](../adr/001-temporal-durable-workflow.md),
  [ADR-005](../adr/005-official-source-authority.md),
  [ADR-012](../adr/012-bounded-page-reader.md),
  [ADR-013](../adr/013-human-attestation-for-unverified-postings.md),
  [ticket 003](003-ashby-adapter.md),
  [ADR-016](../adr/016-scheduled-watch.md) (proposed with this ticket;
  it records the decisions below).

## Why

Postings go stale while they wait for review, and nothing looks at a
pending case again until its approval expires after seven days. A
read-only revisit on 2026-10-10 of the 11 distinct postings behind the
privately retained pre-reset cases (created two to three weeks earlier)
found 5 gone, **none by HTTP 404/410**:

- two careers-site postings: HTTP 200 at the same URL, with a "job not
  found … may have been taken down" notice;
- two job-board ads: one redirect to a category listing, HTTP 200, no
  notice and no JobPosting;
- one SmartRecruiters posting past its application deadline: HTTP 200
  with an "expired" notice; its per-job API still answered 200, with
  `active: false`, and the posting was missing from the company's active
  list.

Of the 6 still up, one board ad had been re-dated under the same URL. No
ATS posting reappeared under a new id; board ads and careers sites were
not searched for reposts. An Ashby posting taken down (the owner's
example, observed 2026-10-11): the board API no longer lists it, the
static page is a 9 KB shell with no JobPosting and no text, and the
rendered page reads "Page not found" with no apply control.

Broad discovery across the market is not this system's job: boards and
careers sites without a clean API are not scraped (v4 §11). The watch
reads the operator's chosen ATS boards through their public APIs at a
fixed cadence, puts new postings through the governed pipeline, and
rechecks what waits for review. Both vendors let a posting be
unpublished and published again under the same id; one supports timed
unpublish-and-republish cycles.

## Scope

1. **Watchlist, a private policy file** (like the official-host
   mapping): `COUNTERSIGN_WATCHLIST_PATH`, JSON `{version, schedule:
   {cron: [...], time_zone}, boards: [{adapter: "ashby" | "greenhouse",
   board}], locations: [...], exclude_titles: [...],
   published_within_days, max_new_cases_per_run,
   max_recheck_model_calls_per_run}`. Validated at worker start, 8-hex content
   fingerprint, unset means no watch. A synthetic example in
   `config/watchlist.example.json` uses UTC and example boards.
   - `locations` (required): case-insensitive substrings matched against
     the posting's structured location texts (Ashby: location, secondary
     locations, address locality and country; Greenhouse: location
     name). A remote flag alone does not pass; a remote posting passes
     when its location text names an allowed region. A posting with no
     location text passes and is counted on the run's audit event: the
     filter may cost an evaluation, never drop a posting silently.
   - `published_within_days` (default 14): the fixed lookback window
     every run applies to the vendor's latest publication timestamp. The
     first run backfills it; later runs see new and republished postings
     and, because the window is fixed rather than "since the last run",
     whatever an earlier run could not admit under its budget stays a
     candidate until it ages out or is taken down. The board listing is
     the queue; the run keeps no state of its own.
   - `exclude_titles` (optional): substrings that drop a posting.
   - No title keywords select postings. Relevance is the evaluator's
     job: it already scores fit, drift and feasibility against the
     operator profile. A filter here may fail by costing an evaluation,
     never by silently dropping a relevant posting.
2. **One Temporal Schedule**, `countersign-watch`, from the file's
   calendar: cron expressions in the file's time zone (for example three
   fixed mornings a week), overlap policy skip, and a one-day catch-up
   window, so a run missed while the host was down fires once when it is
   back. The worker creates or updates the Schedule at startup from the
   validated file and pauses it when the variable is unset. Manual runs
   go through the Temporal UI or CLI; there is no second scheduler.
3. **`WatchWorkflow`, one run per tick**: rechecks, then discovery, each
   under its own budget (item 5), so a run after a large backfill still
   admits new cases.
   - `recheck_pending`: cases in PENDING_APPROVAL whose approval request
     is still pending and whose latest verification predates the run,
     oldest first, whatever created them. Each recheck runs the existing
     verify path unchanged (adapter first, reader otherwise) under
     run-derived ids (`ver-{case}-g{n}-r{run}`, `read-{case}-g{n}-r{run}`)
     and records the reading and verification **without a state
     transition**, and only if the case still waits on the same approval
     request (checked in the write transaction). The recheck skips the
     model only when two existing checks both pass on the fresh
     observation: the normalized text hash equals the last
     verification's, and the previous reading's verified claims still
     bind under the reader's citation check (`_verify_reading`: same
     label, same target). Only a completed reading whose body and claims
     all sit on its first observation can be checked that way; a reading
     that relied on a rendered or followed page runs the reader again.
     Then the decision table runs again at the
     current time, so a deadline that passed since still yields EXPIRED;
     a changed apply target or a new notice fails a check and the reader
     runs. An apply target that reads the same but no longer works is not
     detected, as at first verification. The approval keeps the
     verification pinned at creation; case detail shows the latest.
     APPROVED cases and every other state are not rechecked.
   - `discover_postings`: one GET per board, either the Ashby posting API
     (`list_board` beside ticket 003's `lookup`) or the Greenhouse Job
     Board API (`boards-api.greenhouse.io/v1/boards/<board>/jobs`,
     without descriptions). A posting that passes the filter and is not
     known, and was published or republished within the window, becomes
     a case, newest first, up to `max_new_cases_per_run`. The timestamp
     is the vendor's latest publication time: Ashby `publishedAt`;
     Greenhouse `updated_at`, documented on the list endpoint and moved
     by republication and edits, because `first_published` (returned on
     the probed board, documented only for the single-job endpoint) never
     moves on republication and would hide a posting republished after a
     gap longer than the window. An edit can pull an older posting back
     into the window at the cost of one evaluation, after which it is
     known. Known means a case in any state whose URL parses
     to the same posting: Ashby `(board, job id)`; Greenhouse job id
     under either hosted spelling (`boards.` or `job-boards.greenhouse.io`).
     A case is created in DISCOVERED with actor `watch`, the run as
     request id and an id derived from the posting, so a retried
     Activity or a replayed run cannot create a second case. Its URL is
     the Ashby `jobUrl` or the hosted Greenhouse posting URL. The run
     starts each new case's `JobIntelligenceWorkflow` as an abandoned
     child with the API's workflow id and reuse policy, and every new
     case runs the full pipeline, evaluator included. A known case that
     ended by source closure (CLOSED_OR_STALE with a CLOSED or EXPIRED
     latest verification), whose posting the board lists with a
     publication timestamp later than that verification, gets one audit
     event `case.reappeared`, keyed on the case alone and checked before
     the window test, so the first return after any gap is recorded and
     nothing after it. A key on the case and the timestamp would record
     every later edit of a Greenhouse posting as another republication,
     since `updated_at` moves on edits too; for Greenhouse the event
     therefore means "republished or edited after the closure". The
     timestamp condition keeps a posting that never left the board (its
     page closed while the API still lists it) from being recorded. The
     case is not admitted again; the operator re-pastes it for a fresh
     case, whose own lifecycle covers later takedowns.
4. **Closure as decided in ADR-016 (decision 4):** the decision table's
   CLOSED or EXPIRED, or board absence on two consecutive rechecks. The
   first such recheck records UNVERIFIED with that reason; a taken-down
   API-backed posting can leave a shell with no notice, so the page alone
   cannot decide it.
   - *Absence*, observed by one recheck: the Ashby lookup in the verify
     path read a healthy board (HTTP 200 from the API host, parsed, at
     least one posting; an empty board is now noted "unavailable") that
     does not list the job, and no observation of the reading carries a
     JSON-LD JobPosting. Only Ashby is API-backed in this sense;
     Greenhouse has no adapter in the verify path, so its pending cases
     close through the decision table or approval expiry.
   - *Consecutive* counts the case's own verifications, not schedule
     ticks. Absence overrides every status but CLOSED/EXPIRED: a recheck
     that observes it records UNVERIFIED with the reason "absent from its
     ATS board and no JobPosting on the page", or CLOSED when the case's
     preceding verification carries that reason. A run that does not
     recheck the case (budget) neither breaks nor extends the chain; a
     recheck that does not observe the absence (posting listed, a
     JobPosting on the page, board unavailable or empty) breaks it.

   No reopening. Closing
   goes through the case's running workflow. The watch run sends a new
   Signal, `source_closed`, carrying the approval request id it was
   computed for; it competes with `submit_decision` for one outcome slot
   in the approval wait, and the first to arrive wins. A closure runs the
   existing expiry Activity with the reason "source closed on recheck:
   <verification reason>", which expires the approval and moves the case
   to CLOSED_OR_STALE; from then on the decision API answers 410, as for
   any expired request. A decision that arrives in the moment between a
   winning closure and the expiry is dropped like any second decision.
   If the decision wins, the closure Signal is dropped and the recheck's
   CLOSED verification stays visible on the case.
5. **Two budgets.** `max_new_cases_per_run` bounds discovery; each
   admitted case is charged its worst case of eight model calls (six for
   its reading, two for its evaluation with one repair), spent in the
   child after the run has ended. `max_recheck_model_calls_per_run`
   bounds rechecks: a recheck starts only while the remaining budget
   covers its worst case of six (three reader steps with one repair
   each), is charged six on admission, and the Activity's return value —
   the calls it made, 0 on the API path or an unchanged hash — credits
   the unused part back; rechecks go oldest first and stop when no
   worst case fits, the rest waiting for the next run. The budget is
   never exceeded, and neither side can starve the other. Not covered: Temporal
   retries after provider outages (up to four attempts per Activity) and
   evidence rounds the operator starts. Actual usage stays on every
   reading and evaluation; the run's audit event records what it
   admitted and charged.
6. **Trail.** Per run one audit event `watch.run`: watchlist version and
   fingerprint; boards read and their HTTP outcome; postings seen,
   filtered out, already known and created; cases rechecked and closed;
   model calls charged. Per case the existing `case.created` (actor
   `watch`), the recheck's reading and verification, and the usual
   transition events with reasons. The case page needs no change.

## Out of scope

- Rechecking APPROVED cases. The product cannot record a submission yet,
  so an approved case would be rechecked until its posting closes; after
  approval the operator acts at once.
- Dedupe on paste. A URL pasted after the watch created its case makes a
  second case; its cost is bounded (one more evaluation, a few rechecks
  until the approval expires), and deliberate re-pastes for comparison
  keep working. A pasted link that redirects onto a watched posting is
  not recognised either.
- A posting that moved off its URL without a notice (the board-ad
  redirects above). The reader has no evidence kind for it and records
  UNVERIFIED, so such a pending case closes by approval expiry. Revisit
  with the watch's own data: either a deterministic rule (same URL,
  formerly a JobPosting, now landing elsewhere without one) or a reader
  evidence kind, which is a prompt and contract change (ADR-012
  amendment).
- Reposts under a new id, phase-2 canonical merge and reopening closed
  cases: none observed on ATS boards so far. A republication under the
  same id is recorded (`case.reappeared`), not reopened.
- A candidate not admitted under the budget that leaves the lookback
  window before the next run: accepted as a known trade-off of the
  stateless design.
- Importing scan reports (v4 §11 item 5, the scan-report import
  adapter): the natural next ticket for sources without an API.
- Lever and SmartRecruiters APIs, re-evaluation on content change,
  digests and notifications, a watchlist UI, a `source_key` column, and
  LLM triage of new postings, which would widen the MODEL invocation
  class (ADR-011).

## Touch points

- `policy/watchlist.py` + contract (loader, validation, fingerprint,
  filter); `capabilities/ats/ashby.py`: `list_board`, and its terms note
  updated for scheduled reads; `capabilities/ats/greenhouse.py`:
  `list_board`, posting-URL parsing, terms note.
- `activities/case_activities.py`: the verify path factored so a recheck
  runs it under recheck ids without a state transition;
  `activities/watch_activities.py`; `domain/ids.py`: recheck ids;
  `repo`: recheck candidates, known postings, `watch.run` audit.
- `workflows/watch.py`; `workflows/job_case.py`: `source_closed` and the
  single outcome slot in the approval wait.
- `worker`/`runtime`: create or update the Schedule; Compose and
  `.env.example`.
- Tests: unit (watchlist validation and fingerprint; the location and
  exclusion filter, including remote without an allowed region; posting
  identity for both Greenhouse spellings; budget arithmetic; the
  Schedule spec built from the file); workflow, under time skipping with
  the watch started directly (a run creates cases within the budget,
  skips known ones and starts each child once; a replayed run creates
  nothing twice; a recheck that returns CLOSED closes a waiting case
  through the Signal; a decision and a closure give one outcome in either
  order; a recheck of a case that left PENDING_APPROVAL writes nothing;
  rechecks stop when no worst case fits and credit back unused calls;
  an unchanged page with a passed deadline yields EXPIRED without a
  model call, and a changed apply target under an unchanged text runs
  the reader; a republished closed posting records `case.reappeared`
  once, also after a gap longer than the window); PostgreSQL (recheck ids and the audit
  event within column widths); contract (old records unchanged).

## Acceptance and verification

- A watchlist naming one Ashby and one Greenhouse board: a run creates
  only postings that pass the filter, newest first and within the
  budget, each through the normal pipeline to PENDING_APPROVAL; the next
  run creates nothing for postings already covered, admits candidates
  the budget left over while they are still in the window, and rechecks
  the pending cases with zero model calls on the Ashby API path or an
  unchanged page.
- A pending case whose page now returns 404/410, shows a cited closed
  notice or a past `validThrough` closes on the next run, with the
  trail. A pending case whose posting left a healthy board and whose
  page shows no JobPosting is UNVERIFIED with that reason on the first
  run and closes on the second. A republished posting of a closed case
  is recorded as reappeared, not reopened. An approved case is not
  rechecked.
- A decision and a closure arriving together produce one outcome.
- Run and report the usual checks with counts and the disposable
  database name.
- Live: two weeks at the operator's cadence on the owner's deployment;
  at least one discovered case reaching approval, and any takedown among
  pending cases recorded either as a closure or as UNVERIFIED followed by
  expiry. Identifiers stay in private notes.

## Decisions for the owner before implementation

- Decided 2026-10-11: ADR-016 written (proposed); the approval becomes
  EXPIRED with the reason, no new status; the first run backfills 14
  days and later runs use the same window.
- Decided 2026-10-11: ADR-016 accepted, including the board-absence
  closure on two consecutive rechecks and one `case.reappeared` per
  closed case.
- Whether the hosted Greenhouse posting URL resolves for a board with
  its own careers site: checked read-only on 2026-10-11 on three public
  boards whose `absolute_url` points at the company site. The hosted URL
  answered 200 after two or three redirects onto that site's posting
  page. Such a case is therefore verified on the company host, which is
  official only when mapped (ticket 002); otherwise it reaches at most
  OPEN_THIRD_PARTY_ONLY, as a pasted company link does.
- Budget and cadence values (private file).

## Completion record

Implemented 2026-10-11 in code commit `a15cc23` on branch
`scheduled-watch`, on top of `b15472c`. The implementation includes:
`contracts/watchlist.py` and `policy/watchlist.py` (loader, 8-hex
fingerprint, filter), `config/watchlist.example.json`; `list_board` for
Ashby and Greenhouse with a shared `fetch_board` in `capabilities/ats`,
and `greenhouse.posting_ref`; `recheck_case` beside `verify_status`,
which now share `_read` and `_record`; `reread_unchanged` in the page
reader; `activities/watch_activities.py` (`plan_watch_run`,
`discover_postings`, `create_watch_cases`, `record_watch_run`);
`workflows/watch.py`; the `source_closed` Signal in `job_case.py`;
`watch_schedule` / `sync_watch_schedule` in `runtime.py`, called by the
worker; recheck ids and `case_workflow_id` in `domain/ids.py`; budget
units in `domain/watch.py`; Compose and `.env.example`. No schema
change and no migration.

Choices made within the ticket:

- The run key is the first eight characters of the watch run's Temporal
  run id. It suffixes the recheck ids (`ver-…-g1-r<key>`), is the
  request id on `case.created`, and identifies the `watch.run` event.
- The watchlist is loaded once per run by `plan_watch_run` and carried
  through the run, so one run uses one revision. A run with the variable
  unset fails visibly (`PolicyError`), though the worker pauses the
  Schedule in that case.
- A posting's case id is a UUIDv5 of its key (`ashby:<board>:<job id>`,
  lower-cased; `greenhouse:<job id>`). Case URLs are built from the board
  and the job id (`jobs.ashbyhq.com/<board>/<id>`,
  `job-boards.greenhouse.io/<board>/jobs/<id>`) so they always parse back to
  their key.
- Discovery reads the boards in one Activity and writes nothing but
  `case.reappeared`; cases are created by a separate Activity, so a
  retried discovery cannot hide a case it created from its own known
  check. Child starts that find their id taken are skipped.
- Rechecks run one at a time, oldest latest verification first. A
  recheck that fails after its retries is counted, keeps its worst case
  charged and does not stop the run. A retried recheck whose first
  attempt committed returns the stored record. Call counts are not
  stored, so it is charged the worst case when that reading has any
  model step (step 1 on). Usage is not the test: calls that all fail
  validation leave a reading without usage.
  A closure Signal to a case workflow that has already ended is skipped
  and not counted as closed.
- An empty Ashby board is now noted `ashby: unavailable (empty board)`
  instead of `ashby: not published`, so absence means a healthy board.

Review (`/code-review high`, nine findings). Fixed: the tracked example
and tests named a region tied to the operator and now use neutral
places; `without_location` counted postings later dropped by the window
and now counts only candidates; the absence reason carries a comment
that rewording it restarts pending chains. Left as they are, for the
owner to see: the absence chain compares stored reason text (no schema
change); each recheck of an Ashby case reads its whole board again, as
ticket 003's verify path does (blobs are content-addressed); discovery
and the recheck query load every case per run, fine at this scale; an
unparseable listed URL fails discovery by assertion, reachable only
through a board named `.` or `..`; `sync_watch_schedule` has no
automated test, because Temporal's time-skipping server has no
Schedules; the four-line `_workflow_id` helper is duplicated, because
strict pyright rejects importing the private one. Not observed yet: that
Ashby moves `publishedAt` on republication. If it does not, a returning
Ashby posting is never recorded as reappeared.

Owner review (2026-10-11): the retry path took a reading without
usage as one without model calls. A reading whose calls all failed
validation is stored without usage, so the first attempt reported 2
calls and its retry 0, and a lost completion would have credited the
whole worst case back. Fixed as above, with a reproducing test.

Two reproduced review findings remain: the pending-request check and
recheck write do not serialize with a concurrent decision, so a late
recheck of an old round can become the next round's pinned verification;
and the Ashby listing drops secondary-location address locality/country
fields, so a posting can fail the location filter despite a matching
secondary address. Both remain follow-ups in this implementation.

Also observed: `WatchWorkflow` started without arguments makes the
first payload serialization of a fresh process happen inside the
workflow sandbox, which logs a one-time "pydantic_core was imported
after initial workflow load" warning; results are unaffected.

Verification of the working tree, existing `.venv`, Python 3.14.3:
`ruff check .` and `ruff format --check .`: passed (formatter: 130
files); `pyright`: 0 errors; `lint-imports`: 3 contracts kept;
`pytest -ra`: **348 passed, no skips** (295 before; 53 new), with
disposable PostgreSQL database `countersign_watch_004_retry` at
`127.0.0.1:5435` (dropped afterwards), SQLite fixtures elsewhere,
Temporal's test server and local Chrome. Board and page responses in the
tests were faked and model providers were fakes. Web typecheck and build
passed (the web app is unchanged). No commit, deployment, Schedule
creation or live model call was made.

Code commit `a15cc23` was then verified in a fresh detached worktree and
Python 3.14.3 venv, with Python dependencies constrained to the reviewed
environment's versions and front-end dependencies installed from
`pnpm-lock.yaml`: `.venv/bin/ruff check .` and
`.venv/bin/ruff format --check .` passed (129 files), `.venv/bin/pyright`
reported 0 errors, `.venv/bin/lint-imports` kept all 3 contracts, and
`.venv/bin/pytest -ra` reported **348 passed, no skips**. The disposable
PostgreSQL database was `countersign_watch_code_a15cc23` at
`127.0.0.1:5435` (dropped afterwards); other database fixtures used
SQLite, with Temporal's test server and local Chrome. Private profile,
mapping, watchlist and salary policy inputs were unset, and source
responses and model providers were fakes. `pnpm --dir apps/web typecheck`
and `pnpm --dir apps/web build` passed. This verification did not deploy,
create a Schedule or invoke a live model.

Not yet verified: the Schedule on a real Temporal server (create, update,
pause, resume), and the live acceptance above.
