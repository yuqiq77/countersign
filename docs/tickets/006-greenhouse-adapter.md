# Ticket 006 — Greenhouse Job Board API as the official ATS observation

- Status: implemented.
- Slice: Slice 2, the second ATS adapter (v4 §11 puts Greenhouse first;
  §24's kill rule keeps it).
- Basis: [ADR-005](../adr/005-official-source-authority.md) (an official
  ATS API is tier 1), [ADR-012](../adr/012-bounded-page-reader.md) (the
  adapter is a code-initiated observation), the pattern of
  [ticket 003](003-ashby-adapter.md) and the finding of
  [ticket 004](004-scheduled-watch.md); v4 §§11–12.

## Why

A watched Greenhouse board's hosted posting URL redirects to the
company's careers page, where the job is embedded, and the case stays
UNVERIFIED / WAIT under the page's generic title (ticket 004); a second
posting on the same board read empty on 2026-11-02. The embed
is an iframe. A render returns only the main frame's document
(`page.content()`), and a frame's source is no control the reader can
follow (ADR-012), so the job body and its form never reach an
observation; widening ADR-015 would not change that. Greenhouse
documents a public, unauthenticated Job Board API for building such
careers pages (docs.greenhouse.io/job-board.html). Its per-job endpoint,
`GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>`,
returns the title, the description (`content`), the location,
`absolute_url` (for an embedded board, the careers page with
`?gh_jid=<id>`), `company_name`, `internal_job_id` (null on a prospect
post) and dates, `application_deadline` among them. Verification reads
it as ticket 003 reads Ashby's API.

## Scope

1. **URL match.** A hosted posting URL (`posting_ref`, either spelling)
   anywhere on the fetch's way: the requested URL, a redirect hop or the
   final URL. Ticket 003 matches the final URL only, but the problem case
   redirects off the ATS host. `posting_ref` refuses a dot-segment board,
   encoded or not, as Ashby's does. Careers-page, embed and API URLs are
   not adapter cases.
2. **Lookup.** One GET of the per-job endpoint (no query parameters,
   default body cap, time from the reading's 120 seconds); as in ticket
   003, only a 200 from `boards-api.greenhouse.io` itself counts. The job
   is published when the response parses, names the requested `id`, has
   an `internal_job_id` (null marks a prospect post, a general-interest
   post rather than an opening), an http(s) `absolute_url` and no past
   `application_deadline`. Anything else is a note on the reading, and
   the reader runs unchanged: a 404 is `greenhouse: not served (http
   404)`, kept apart for decision 5 and the reviewer; errors, other
   statuses and malformed responses are `greenhouse: unavailable (…)`;
   the rest say what failed (`greenhouse: prospect post` and the like).
3. **Published: OPEN from the API, with no model call.** A code-made
   reading as in ticket 003: observation 0 is the fetched page,
   classified by where it landed; observation 1 is the API response
   (`how: API`, OFFICIAL_ATS, stored as fetched, extractor
   `ats-adapter/greenhouse` `0.1.0`) with the job's title, company and
   text (item 4) and an `Apply` control to `absolute_url` that carries
   the `ATS_LINK` entry. The decision table is unchanged: OPEN /
   OFFICIAL_ATS unless an official page (hosted, or a mapped host) shows
   404/410 or a past `validThrough`, which still wins; an unmapped careers
   page's 404 or date is recorded under OPEN (decision 2). Rechecks take
   the same path, with no model call while the job is published.
4. **Description text.** The location name, then the visible text of
   `content`. The documentation says the description's HTML is converted
   to entities, though its single-job example looks double-escaped
   (`&amp;lt;p&amp;gt;`). One live response settled it (completion
   record): entities once, so the text unescapes once.
5. **Body and title.** A hosted page with text stays the body, as Ashby's
   does. Otherwise (an embedded board's careers page, or a page without
   text) the API observation is the body if its description is not empty:
   the evaluator reads the official text, the approval pins it, and its
   salary counts as official. Code that turns a stored body back into text
   uses the extractor its snapshot records, for the evaluator,
   `content_changed` (only between bodies of one extractor, so a page
   followed by an API body records None) and a retried verification's
   text length. When the API supplied the body, `verify_status` also sets
   the case's title, and its company hint from `company_name`, in the same
   transaction; rechecks leave case facts alone.
6. **Prospect posts in the watch.** The board listing includes prospect
   posts and `list_board` ignores `internal_job_id`, so the watch can
   admit one as a case today. It reads the field, skips null ones and
   counts them in the board's note on `watch.run`.
7. **Authority and terms.** `boards-api.greenhouse.io` joins the ATS host
   list, as `api.ashbyhq.com` did at ticket 003's closeout. The careers
   page the API names gains nothing (ticket 002), and no reader or render
   rule changes. The terms note: public GET endpoints, no credentials,
   documented for careers pages, now at docs.greenhouse.io; one GET per
   verification or recheck.

## Out of scope

- Careers-page and embed URLs (decision 4); board-absence closure
  (decision 5).
- Compensation: the parameter table says `pay_transparency=true` adds
  `pay_input_ranges`, but the single-job example shows them without it;
  this ticket reads neither. `first_published` and `updated_at` decide
  nothing.
- Regional Greenhouse hosts, named by neither the documentation nor the
  host list; Ashby's adapter; the decision table; replaying the adapter;
  other ATS adapters; the v4 text.

## Known limits kept

- Greenhouse cases still close only through the decision table or
  approval expiry, and careers-page URLs still read empty.
- A job the API no longer serves still reaches the reader and the
  evaluator: when its hosted URL redirects to the company's home page,
  the evaluator reads the home page (seen live on 2026-10-30).
- The evaluator sees a salary only where the description states one.
- On an unmapped careers page, a closure the page shows before the API
  drops the job goes unseen.
- The published path does not render; the live check confirms that
  hosted pages serve the description in their HTML.

## Touch points

- `capabilities/ats/greenhouse.py`: `posting_ref`, `lookup`, the per-job
  contract, the text function, `internal_job_id` in `list_board`, the
  terms note. `capabilities/normalize.py`: the host list.
- `activities/case_activities.py`: `_read` tries Greenhouse when Ashby does
  not apply; `_adapter_reading` takes the reading version and the body
  index; one helper turns a stored body into text by its extractor;
  `verify_status` sets the title.
- `diagnostics/replay_reading.py`: a case whose body is an API response
  replays observation 0, since JSON is not a page.
- No contract, schema or web change and no migration. At closeout: README
  current scope and STATUS.

## Tests

- Unit, `tests/unit/test_greenhouse_adapter.py`: `posting_ref` (both
  spellings, extra segments, a query such as `?gh_jid=<id>` as seen live,
  dot segments; careers-page, embed and API URLs never match); each
  `lookup` outcome of item 2; the text function on
  the confirmed `content` shape; `list_board` skipping and counting
  prospect posts.
- Activity, `tests/workflow/test_greenhouse_verification.py`: a hosted URL
  that lands on an unmapped careers page verifies OPEN / OFFICIAL_ATS /
  `ATS_LINK` without a provider, with the API's text and title reaching
  the evaluator, `content_changed` None and the case title set; a hosted
  page with text keeps its body; an official page's 404/410 or past date
  still decides; other outcomes run the reader with their note; a match
  through a redirect hop; a published recheck makes no model call.
- Lost completion: a retried recheck of an adapter reading is charged
  nothing; a retried `verify_status` returns the stored verification with
  the title its first attempt wrote.
- End to end: a Greenhouse case reaches approval with only the
  evaluator's model call, as in ticket 003.
- Fixtures that use a hosted Greenhouse URL as a plain reader page
  (`JOB_URL` in `tests/workflow/support.py`, the watch tests' Greenhouse
  case) move to an ATS host without an adapter, such as `jobs.lever.co`.

## Acceptance and verification

- One live per-job response confirms `content`'s escaping before the text
  function and its tests are fixed.
- The tests above pass in the standard checks, reported with counts and
  the disposable PostgreSQL database named, no skips; web checks only if
  the case page changes.
- Live: ticket 004's pending Greenhouse case, if still pending, is
  rechecked OPEN / OFFICIAL_ATS, but its WAIT evaluation, made on the
  careers page's text, and its title stay, since rechecks leave case facts
  alone. A REQUEST_EVIDENCE round (ticket 001) re-verifies and
  re-evaluates it; otherwise it expires and is pasted again, since the
  watch never admits a known posting twice. A fresh case on an embedded
  board verifies OPEN and is evaluated on the description under the API
  title. Identifiers stay in private notes.

## Decisions

Decided 2026-11-02: decisions 1 to 3 as drafted; decision 5 stays later.

1. **Body.** Drafted: the API description only where the page cannot be
   the body (item 5). Using it always would give every board one rule but
   lose a pay range the hosted page shows.
2. **Unmapped careers page against the API.** Drafted: the table as it
   is, so the API governs and the page's 404 or past date is recorded.
   Letting any page's closure win would change the table for every
   reading.
3. **`application_deadline`.** Drafted: a passed deadline withholds OPEN
   and the reader runs. Deciding EXPIRED from it needs an expiry input
   that is not JSON-LD, a contract change.
4. **Careers-page URLs.** Ticket 005's search queries
   `site:job-boards.greenhouse.io`, so it returns hosted URLs: the one
   Greenhouse result of the first live runs was a hosted URL with
   `?gh_jid=<id>` appended, which item 1 covers. A careers-page URL can
   come only from a query without a site restriction and stays
   unreadable. Drafted: no rule until the trial returns such URLs; then
   parse the embed's frame URL, which names board and job, or read the
   board from a careers page's embed script, a rule of its own.
5. **Board-absence closure, drafted as later.** On a 404 the adapter would
   read the board listing once, and a healthy board without the job would
   count as ADR-016's absence, so Greenhouse cases would close after two
   consecutive rechecks, as Ashby cases do. Until then a 404 is `not
   served`, the reader runs, and Greenhouse cases close through the
   decision table or approval expiry, as now.

## Completion record

Implemented 2026-11-02 on top of `b4b747e`, uncommitted, as scoped:
`posting_ref` hardened; `lookup` and `describe` over a per-job contract;
the prospect skip in `list_board`; the body rule in `_adapter_reading`;
`_body_text` for the evaluator, `content_changed` and the retry path;
the title in `verify_status`; the replay of observation 0 for an API body;
`boards-api.greenhouse.io` in the host list. After review, the shared
`capabilities/ats` helpers and Ashby's board path changed too (below). No
contract, schema or web change and no migration.

Live check, 2026-11-02, on Greenhouse's own public board, unrelated to the
operator: `GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>`
answered 200, `application/json`, 13.7 KB. `content` held the HTML as
entities once (170 `&lt;`, no `<`, no `&amp;lt;`), and one unescape left
no entity. The job's `absolute_url` was
`https://job-boards.greenhouse.io/<board>/jobs/<id>?gh_jid=<id>`; the
response also carried `departments` and `offices`, a null
`application_deadline` and no `pay_input_ranges`. A nonexistent id
answered 404. All 20 listed jobs carried `internal_job_id`.

Choices made within the ticket:

- The per-job response must carry `internal_job_id` (absent reads as
  malformed), while the listing skips only an explicit null: a listing
  without the field drops nothing, as ADR-016's filters fail open.
- A response for another `id` is unavailable. A `mailto:` or missing
  `absolute_url` is "published without an absolute URL".
- A job id must be ASCII digits. With dot segments and slashes refused, a
  watchlist board named `.` or `..`, or one with a slash, fails
  discovery's assertion, as an Ashby one already did (ticket 004).
- An official-host mapping that lists `boards-api.greenhouse.io` is now
  refused at worker start, as for Ashby's API host.
- Fixtures: `JOB_URL` and the watch tests' reader page moved to
  `jobs.lever.co`; the reappearance test answers the API with 404; the
  empty-page test no longer renders: with Chrome set, its step-0
  pre-render had loaded the real host.

Retry edges: the adapter makes plain GETs (the fetcher does not retry) and
no provider call. A retried `verify_status` or `recheck_case` returns the
stored record, charged nothing on the adapter path, and the title is
written in the verification's transaction. No client request changed.

Code review (2026-11-03, five findings), all fixed, each with a test that
failed against the code before the fix:

1. The evaluator was given the API endpoint as the posting's URL; an API
   body now passes the case's own URL.
2. An encoded slash in a board (`x%2F..%2F…`) passed `posting_ref` and
   reached the API path raw, so a lookup could read another path on the
   API host. Both adapters now refuse a board whose decoded name is a dot
   segment or holds a slash or backslash, and quote the decoded name
   afresh in the API path. Ashby had the same gap and gets the same fix.
3. "Not served" compared the shared helper's wording. `fetch_board` now
   returns `Unavailable` with the status the API host itself answered, and
   a 404 after a redirect off that host is no longer "not served".
4. A recheck whose API body gave way to a page recorded `content_changed`
   None. It now records a change; a careers page followed by the API body
   still records None.
5. Replay and the verify path used different predicates for an API body.
   Both now use `is_api_body`, by the adapters' shared extractor prefix.

Verification of the working tree, venv on Python 3.14.3 pinned to the main
venv's package versions: `ruff check .` and `ruff format --check .`
passed (141 files); `pyright` 0 errors; `lint-imports` 3 contracts kept;
`pytest -ra` **447 passed, no skips** (397 before the ticket, 50 new) on
disposable PostgreSQL database `countersign_t006_review` at
`127.0.0.1:5435` (dropped afterwards), with local Chrome. Web checks not
run: the web is unchanged. Nothing was committed, deployed or rebuilt.

Left: deployment and the live acceptance; decisions 4 and 5; README and
STATUS at closeout.
