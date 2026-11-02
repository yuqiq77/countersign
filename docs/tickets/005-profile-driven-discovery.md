# Ticket 005 — Profile-driven discovery: brief, search and screen

- Status: approved 2026-10-22; deployed 2026-10-29; live trial underway.
- Slice: Slice 2.
- Basis: [ADR-017](../adr/017-profile-driven-discovery.md) (accepted
  2026-10-22), [ADR-016](../adr/016-scheduled-watch.md),
  [ADR-011](../adr/011-bounded-company-research-loop.md),
  [ADR-012](../adr/012-bounded-page-reader.md),
  [ADR-005](../adr/005-official-source-authority.md);
  [v4 §11, §13.3, §15](../job-search-agent-architecture-v4.md).

## Why

Discovery runs outside the system today, so the operator keeps two
statements of intent, two inventories and two relevance judgments, and the
watch reaches only boards the operator already knows. ADR-017 moves
discovery inside: a brief derived from the profile, a search driven by it,
one coarse screen for search and board candidates, a record of every
candidate, and four budgets shared by turns. This ticket builds that and
the side-by-side that decides whether the external agent can be retired.

## Scope

1. **Policy.** The watchlist file (`contracts/watchlist.py`,
   `policy/watchlist.py`) gains `max_searches_per_run` (0 turns the search
   off) and `max_discovery_model_calls_per_run`. `max_new_cases_per_run`
   now counts both sources; `max_recheck_model_calls_per_run` is unchanged.
   `locations` doubles as the search's regions. `boards` may be empty when
   the search is on; a file with neither source fails validation. The
   example file and its tests follow.

2. **Discovery route.** The brief, the search and the screen share one
   variable, `COUNTERSIGN_DISCOVERY_MODEL_ID`: the Haiku-class model when
   the provider is `anthropic`, and the Sonnet-class model as the fallback
   if Haiku's search filtering or task quality falls short in the trial.
   The evaluator keeps `COUNTERSIGN_MODEL_ID`. The fake provider stays the
   default and gains canned briefs, searches and verdicts.

3. **Brief.** Contract `SearchBrief`: required lists of short strings
   (role families, skills, regions, languages, roles to exclude), each
   entry at most 60 characters and each list at most 12 entries, with no
   free-text field. Activity `derive_brief` (`discovery-brief/0.1.0`) runs
   when no brief exists for the current profile fingerprint, stores it in
   `search_briefs` (fingerprint, brief, prompt version, route, usage,
   created_at) and returns it. Without a profile there is no search, and
   the run records why. `GET /v1/discovery/brief` returns the current
   brief; the case list page shows it in a "Search brief" view.

4. **Search.** Activity `search_postings` (`discovery-search/0.1.0`) makes
   one gateway call with the provider's web search tool (dynamic filtering
   on), carrying the brief, the regions and its instructions only, and
   follows `pause_turn` continuations. Each request, the first and every
   continuation, is one model call charged to the discovery budget.
   Searches are counted separately and cumulatively: each response's usage
   reports the searches it made, which are deducted from the run's
   `max_searches_per_run`, and every request carries `max_uses` equal to
   what remains, so a continuation never gets a fresh allowance. If the cap
   runs out while the model is paused, the continuation must not search
   again; the probe below establishes how. The Activity stores each
   response's queries as it goes, so the queries survive a failure. It
   returns the candidates
   (`SearchCandidate`: URL, title, company, location text, reason; all
   required), the queries issued, the result URLs and the calls made. Code
   drops, and counts, a candidate whose URL (scheme, host and path,
   normalized) is not among the result URLs; a candidate on a
   terms-restricted host becomes a lead.
   **First implementation step:** a live probe on the discovery route of
   three things. Whether the search tool and structured output combine; if
   not, the search ends with a strict `submit_candidates` tool (tool choice
   `auto`, the prompt asks for it) validated against the same contract.
   Whether the usage field counting searches is present on every response.
   And how a paused request is continued without searching (tool choice
   `none`, or ending the step there). The probe and its results are
   recorded here.

5. **Terms list.** `capabilities/terms.py` lists hosts whose terms forbid
   automated access, LinkedIn first, each with a terms note, matched on the
   host and its subdomains. It is code, like the ATS host list, and apart
   from authority classification. It gates two points. Admission: a search
   candidate on a listed host is a lead (item 4). The reader: no RENDER or
   FOLLOW on or onto a listed host, for every case, pasted ones included,
   since v4 §5 rules out browser automation against a source's terms. A
   redirect onto a listed host is followed as for any page, in every fetch
   the case makes: the snapshot and the verification at intake, each
   recheck while the case waits, and any retry, each following up to five
   redirects, read-only, with no login and no stored session. The
   verification's reason names the host. Stopping the fetch at that hop
   would need the fetcher to know which cases are automated.

6. **Candidates.** Migration `0002` adds `discovery_candidates`: the
   posting key (the ATS key where the URL parses, else the URL normalized:
   lower-case host, no fragment, tracking parameters removed, other query
   parameters kept), URL, source (`BOARD` or `SEARCH`), origin (the board,
   or the query), title, company, location texts, a board posting's
   publication timestamp, first and last seen, status (`UNSCREENED`,
   `PASS`, `DROP`, `LEAD`, `ADMITTED`), reason, the verdict's inputs
   (profile fingerprint, screen prompt version, route, listing hash),
   first passed, case id and usage. Activity `record_candidates` upserts
   what both sources returned in the run (a posting both return stays with
   the source that recorded it first), sets aside what a case already
   covers, and returns the two screening queues and the two PASS queues in
   their orders.

7. **Screen.** Activity `screen_candidate` (`discovery-screen/0.1.0`), one
   candidate per call: the profile in the cached prefix, the listing facts
   in the suffix (title, company, location texts, and from Ashby the
   department, the team and the first 1,500 characters of the plain
   description, which `list_board` starts to parse; Greenhouse's list
   endpoint carries none of these). Contract `ScreenVerdict`: `verdict`
   (`PASS` or `DROP`) and `reason`. A refusal, a contract failure after one
   repair or a timeout records PASS with the reason "screen failed: …".
   Staleness is a pure function of the stored inputs and the current ones
   (ADR-017 decision 4): board postings are screened again when listed;
   stale search drops last seen within `published_within_days` are queued
   again.

8. **Turns and budgets.** A pure function in `domain/discovery.py`
   interleaves two ordered queues: one item from each in turn, the first
   turn alternating per run (each run records which source went first; the
   next starts with the other), the remaining queue continuing when one
   runs out. Units in `domain/watch.py`: the brief 2 calls (one repair),
   the search its call cap, a screen 2 calls (one repair); each is charged
   before the call and credited back after, ADR-016's rule. The discovery
   budget goes to the brief, then the search, then screening; admission
   takes turns over the two PASS queues up to `max_new_cases_per_run`.

9. **Workflow.** `WatchWorkflow`: plan, rechecks (unchanged), brief (when
   due), search, board listing (`discover_postings` returns candidates now,
   not admissions), `record_candidates`, screening turns, admission turns,
   `create_watch_cases` (which marks candidates `ADMITTED`), child
   workflows, `record_watch_run`. The search Activity gets a longer timeout
   than the others.
   **Failures.** A search failure never stops the boards. `derive_brief`
   and `search_postings` return model-level failures (a refusal, a
   contract failure after one repair, a provider timeout) as outcomes with
   a reason, not as exceptions, and mark them non-retryable where a retry
   would repeat the same answer. The run then records the failing step and
   its reason, skips the search, and goes on with board listing,
   screening, admission and its audit. An Activity that still fails after
   Temporal's retries is caught the same way. Charges follow ADR-016: a
   failed step keeps its worst case charged unless its outcome reports the
   calls made, and searches made before a failure stay counted. A
   `screen_candidate` Activity that fails after retries leaves its
   candidate unscreened for the next run (a model-level failure inside it
   passes the candidate, item 7). `record_watch_run` runs in a `finally`,
   so a run that fails also tries to write its audit event, naming the
   step that failed; a failure of the audit write itself is not covered.

10. **Trail.** `WatchRunSummary` gains searches made; search candidates
    accepted, rejected by the URL check, and leads; screened, split into
    PASS, DROP and failed open; both queue lengths; discovery model calls
    charged; and the source that went first. Queries are stored with the
    candidates and never logged.

11. **Lists.** `GET /v1/candidates?status=DROP|LEAD`, last seen within 30
    days, newest first. The case list page gains "Screened out" and "Leads"
    views (title, company, source, reason, link) with a "Start case" button
    that sends the URL through the normal intake. A case admitted through a
    failed screen carries a marker in the case list.

12. **Side-by-side.** A private script under `data/operator/` compares the
    external agent's reports for the trial weeks with the stored
    candidates. It is not tracked; the owner runs it.

## Live probe — 2026-10-22

Three requests on `claude-haiku-5-5` with synthetic input (a backend role
in a city the operator does not search; no profile), through the
project's direct API configuration:

- **Search with structured output works.** `messages.parse` with
  `web_search_20260209` and an output contract returned a parsed result.
  The response carries thinking, `server_tool_use`,
  `web_search_tool_result` and code-execution blocks (the dynamic
  filtering) before the final text.
- **Searches are counted in usage.** `usage.server_tool_use.web_search_requests`
  matched the searches made (2 of `max_uses` 2, then 3 of 4). On a
  response that made no search the field is absent, so code reads a
  missing count as zero.
- **`tool_choice: none` stops searching** with the tool still defined, on
  a fresh request and after a turn that searched. A real `pause_turn`
  continuation was not reproduced (it takes ten server-side iterations);
  `none` is the planned way to continue without searching, to be confirmed
  if a pause occurs live.
- **The queries decide what comes back.** Generic queries returned only
  aggregator and listing pages, and the model, told to return individual
  postings from its results, returned none. Queries restricted to the ATS
  hosts (`site:jobs.ashbyhq.com` and the like) returned five individual
  postings on hosted Greenhouse and Ashby pages, all among the result
  URLs. The search prompt draft therefore asks for site-restricted queries
  on ATS hosts and for companies' own careers pages.
- **Usage per search request:** about 18k and 38k input tokens (results
  and filtering code count) and 0.5k to 2k output tokens.

## Out of scope

- Re-evaluating pending cases when the profile changes.
- Canonical merge across hosts (v4 §29), weights for the turns, further
  ATS adapters.
- Capability registry enforcement: the search tool runs at the provider
  (ADR-017).
- The v4 text for §11 and §15 (owner).

## Touch points

- `contracts/watchlist.py`, `policy/watchlist.py`,
  `config/watchlist.example.json`.
- `contracts/discovery.py` (brief, search candidate, verdict, candidate
  views); `contracts/workflow_io.py` (run payloads and summary).
- `gateway/`: the discovery route; a search call with the server tool that
  keeps the raw blocks for queries and result URLs; brief and screen
  calls; fake provider.
- `capabilities/terms.py`; `capabilities/ats/ashby.py` (department, team,
  description excerpt); `agents/page_reader.py` (no RENDER or FOLLOW on
  or onto a listed host) and the verification reason for a fetch that
  lands on one.
- `domain/discovery.py` (turns, staleness, posting key for other URLs);
  `domain/watch.py` (units).
- `activities/discovery_activities.py` (`derive_brief`, `search_postings`,
  `record_candidates`, `screen_candidate`); `activities/watch_activities.py`.
- `workflows/watch.py`.
- `persistence/models.py`, `persistence/repo.py`,
  `migrations/versions/0002_discovery.py`.
- `api/routes.py` (candidates, brief); `apps/web` (two views and the
  brief).
- `.env.example` and Compose (route variables).

## Tests

- Unit: policy validation (search off, no boards, neither source); turns
  (alternation, one queue empty, odd slots across runs); staleness (each
  input); the URL-in-results check; posting keys for other URLs (tracking
  parameters); terms host matching; the brief contract's limits; the
  search cap across continuations (each request's `max_uses` is what
  remains, and the total never exceeds the cap); the reader refusing
  RENDER and FOLLOW on and onto a listed host.
- Workflow, under time skipping with fakes: the brief is derived once per
  fingerprint; a candidate outside the results is rejected; leads are
  recorded and not admitted; screening and admission alternate and respect
  every budget; a failed screen passes; stale drops are queued again after
  a profile change; a brief or search that is refused, fails its contract
  or exhausts its retries leaves the boards screened and admitted and the
  run's audit event written with the failure; a replayed run creates
  nothing twice; rechecks behave as before.
- PostgreSQL: migration `0002` within column widths, timezone round-trips.
- API: the candidates and brief endpoints. Web: typecheck and build.

## Acceptance and verification

- Local: the standard set with no skips (disposable PostgreSQL, Chrome),
  web typecheck and build.
- Live probe: the search with structured output on the discovery route,
  recorded here.
- Deployed only after ticket 004's two-week acceptance ends (earliest
  2026-10-28), so its observation window stays clean.
- Live trial with the owner's first budgets: the brief derived and shown;
  search candidates with their queries; rejections by the URL check;
  verdicts and reasons; drops and leads listed; admissions by turns; usage
  per run. Then the two-week side-by-side, after which the owner decides
  on retiring the external agent.

## Decisions

Decided 2026-10-22:

1. The brief, search and screen prompts start as drafts in code; the owner
   edits their wording.
2. The brief is used and shown, with no confirmation step.
3. Leads are listed as found, not screened.
4. A watchlist with no boards (search only) is allowed.
5. The candidates lists cover the last 30 days, and a case admitted
   through a failed screen is marked.
6. One discovery route, with the Sonnet-class model as its fallback.
7. A case whose URL redirects onto a terms-restricted host follows those
   redirects in its own fetches (snapshot, verification, rechecks,
   retries); the reader does not render or follow there.
8. ADR-017's decisions 5 and 7 now say what items 2 and 5 say, so the
   ADR and this ticket give one basis for the code.

## Completion record

Implemented 2026-10-22 on branch `profile-driven-discovery`, subsequently
reviewed and merged into `main`: the policy fields and their validation;
the discovery route; the brief, search and screen calls with draft prompts
(`gateway/discovery.py`); the terms list with its three gates (admission,
the reader's RENDER and FOLLOW, render resources); `list_board` parsing
Ashby's department, team and description head; migration `0002` with
`discovery_candidates`, `search_briefs` and `discovery_searches`; the
four Activities (`derive_brief`, `search_postings`, `discovery_queues`,
`screen_candidate`); the watch run with turns, four budgets, failure paths
and its trail in a `finally`; `GET /v1/candidates`, `GET /v1/discovery/brief`
and the failed-screen marker; the "Screened out", "Leads" and "Search
brief" views.

Choices made within the ticket:

- The search step runs once, with no Temporal retry: a retry would search
  again under a fresh allowance. Its requests are stored per Activity
  attempt, so a retry, were one configured, would still be on record.
- The search has no contract repair: a repair would have to search again.
  A request that ends without output is a failure outcome.
- A search candidate keeps the title it was first recorded with, since the
  title is the model's transcription and would make the verdict stale on
  every run; a board posting refreshes its listing facts.
- The search's one-sentence reason is kept as the candidate's note, shown
  in the lists until a screen replaces it, and never given to the screen.
- A board candidate's company is the board's slug.
- A recording sequence breaks ties between candidates first seen in one
  run.

Review (eight findings). Fixed: a second paused request
dropped the first one's blocks from the conversation; a request ending
without output skipped the query record; "Start case" could create two
cases on a double click; every run read the whole drop history; the terms
refusal text lived in two modules; the page refreshed twice on load; board
candidates reached the screen without a company. Left: a run whose plan
fails writes no audit event, since there is no watchlist identity for the
summary (Temporal records the failure); and a validation error raised
inside the SDK's `parse` loses that request's queries.

Owner review (2026-10-25): three reproduced findings, each fixed with a
test that fails without the fix.

- The SDK's two default retries resent a search request after a read
  timeout with the same `max_uses`. With one request and one search
  allowed, three requests went out and the run recorded one call, and
  searches the provider made before a timeout would have gone past the
  cap. Search requests now go out once, as the step does under Temporal.
  A request that fails without a count is charged its whole allowance, and
  a step whose outcome is lost the whole cap, so the run's search count is
  an upper bound. Brief and screen requests keep the SDK's retries: they
  resend a prompt to the provider, not queries through the search, and
  stay outside the call budget as ADR-016 leaves retries after outages.
- A retried `derive_brief` whose first attempt had stored its brief
  derived a second one and gave it to the search, while the record and the
  UI kept the first. The Activity now returns the stored brief: a retry
  finds it and returns it with the calls its first attempt made, without a
  model call, and an attempt that finds a brief stored while it derived
  returns the stored one.
- "Start case" minted an idempotency key per click and enabled the button
  again after a failure, so when the first request committed its case and
  then failed to start the workflow, or lost its response, the next click
  created a second case. The key now belongs to the candidate
  (`candidate-<id>`, with the id the list returns), so a retry, after a
  reload too, replays it and the API starts the first case's workflow.
  Checked in the browser against a mocked API: two clicks, answered 500
  and then 202, sent the same key.

Verification of the working tree, existing Python 3.14.3 venv:
`.venv/bin/ruff check .` and `.venv/bin/ruff format --check .` passed
(138 files); `.venv/bin/pyright`: 0 errors; `.venv/bin/lint-imports`: 3
contracts kept; `.venv/bin/pytest -ra`: **397 passed, no skips** (351
before; 46 new), with the disposable PostgreSQL database
`countersign_discovery_005_review` at `127.0.0.1:5435` (dropped
afterwards), SQLite fixtures elsewhere, Temporal's test server and local
Chrome. Models and sources were fakes; the only live calls were the probe
above. Web typecheck and build passed.

Deployed on 2026-10-29 from `b4b747e`, with the first budgets in the
private watchlist. The business database was backed up and upgraded
in place from `0001` to `0002`; existing cases, approval pins, decisions
and waiting workflows were preserved. CI on `b4b747e`
([run](https://github.com/yuqiq77/countersign/actions/runs/40965745358),
Python 3.12.15): 394 passed and 3 browser tests skipped, which then
passed in the runtime image (Python 3.12.16). Read-only checks before
and after the cutover are kept privately.

Live trial, three runs: a manual trigger on 2026-10-29 and the calendar
ticks of 2026-10-30 and 2026-11-02. No failure, and every budget held
(searches 2, 5 and 4 of 6; discovery model calls 5, 8 and 6 of 40).

- **Search.** 11 searches returned 13 candidates in total, nine of them
  new, every URL among the results; no lead. The first run's query record
  also held five empty entries, cause not established (no raw response
  was kept); they have not recurred.
- **Screen.** Board candidates: five verdicts, two of them DROP for the
  same design role (screened again after its title changed). Search
  candidates: nine verdicts, all PASS. No verdict failed open.
- **Admission** took turns as designed: the search led on 2026-10-30 and
  filled all five places; the boards led on 2026-11-02.
- **Outcomes.** of the 11 admitted cases: four live, on-target postings
  verified OPEN, two of them found by the search on boards the watch does
  not read. Six search results were already gone or unreadable: five
  closed at verification without an evaluation (404 or a closed notice),
  and one hosted Greenhouse URL redirected to the company's home page and
  was evaluated there.

Findings: five of the eight search admissions were closed at verification,
and one was unreadable. Two passed search candidates waited from
2026-10-30 until 2026-11-02; when admitted, one was verified OPEN and the
other CLOSED. Their status when first queued was not verified. Four of
the closed search cases returned HTTP 404, which a check before admission
could have detected. The queries barely varied between the two calendar
runs, a weekend apart. An aggregator's repost on an ATS host was
classified as an official ATS posting.

Not done: the two-week side-by-side and the script in item 12 (it needs
a sample of the external agent's report); a lead and a URL rejection have
not occurred live. Prompt revisions remain undecided.
