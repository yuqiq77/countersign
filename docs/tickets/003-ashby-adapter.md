# Ticket 003 — Ashby posting API as the official ATS observation

- Status: implemented and verified; code `d537802`, documentation
  committed separately. Live adapter checks recorded on 2026-10-10;
  the final ATS-host classification line awaits worker rebuild.
- Slice: Slice 2, first ATS adapter (owner's choice ahead of v4 §11's
  Greenhouse-first order). Verification only; discovery through ATS APIs
  is a later ticket.
- Basis: [v4 §§11–12, 15](../job-search-agent-architecture-v4.md),
  [ADR-005](../adr/005-official-source-authority.md) (official ATS
  API/page is tier 1), [ADR-012](../adr/012-bounded-page-reader.md)
  (the adapter is a code-initiated observation, like the step-0 render).

## Why

Postings on this ATS are client-rendered shells: the static page carries
only JSON-LD, the apply control appears after a render whose network
policy needed ADR-015, and one live case still rendered empty. The ATS
publishes an unauthenticated JSON API per job board that lists every
published posting with `jobUrl`, `applyUrl` and `isListed`. Verified on
2026-09-28 against two boards: HTTP 200, `apiVersion "1"`, one object per
job with `id`, `title`, `jobUrl`, `applyUrl`, `isListed`, `publishedAt`,
`descriptionHtml`, `descriptionPlain`, location and employment fields;
no per-job endpoint; responses of 0.3 MB and 2.0 MB for 22 and 67 jobs.

## Scope

1. **URL match.** `jobs.ashbyhq.com/<board>/<job-id>` (further path or
   query ignored). Anything else, including the API host itself, is not
   an adapter case.
2. **Lookup.** `GET https://api.ashbyhq.com/posting-api/job-board/<board>`
   through the existing pinned, SSRF-guarded fetcher with the reader's
   remaining time budget. The body cap is raised for this call only, to
   8 MB, because the API has no per-job endpoint; a larger board is
   "unavailable", never a truncated parse. The job is found when a
   published object's `jobUrl` names the same board and job id and its
   `applyUrl` is non-empty. `isListed` is not a condition: per the API
   documentation `false` means the posting is reachable only by direct
   link, which is exactly a pasted link; the value is recorded, not
   judged.
3. **Published → the API carries the apply entry, no model call in
   verification.** The reading is code-made: observation 0 is the fetched
   page as today (the body for evaluation stays the pasted page, whose
   JSON-LD text intake already extracts); observation 1 is the API
   response (`how: API`, authority `OFFICIAL_ATS`, text =
   `descriptionPlain`, one control `Apply` → `applyUrl`); the apply
   entry is `ATS_LINK` on observation 1 citing that control; page
   evidence is added exactly as the reader adds it (a JSON-LD
   `validThrough` on observation 0 becomes `VALID_THROUGH` evidence);
   one step (`step 0`, `DONE`, outcome "adapter ashby: published"), stop
   `DONE`, `prompt_version` `ats-adapter/ashby/0.1.0`, no usage. The
   existing decision table then decides, unchanged: an HTTP 404/410 or a
   past `validThrough` on the page still wins over the API's entry
   (`CLOSED`, `EXPIRED`, the conflict visible as today); otherwise `OPEN`
   from observation 1. The evaluator still runs afterwards and still
   calls the model.
4. **Not published, or API unavailable → the reader runs unchanged.** A
   posting absent from a healthy board is closure evidence in ADR-005's
   terms but only with a second observation; v1 does not close on absence.
   The reader reads the page as today and the outcome of the API attempt
   is recorded on the reading (`adapter: "ashby: not published"`,
   `"ashby: unavailable (http 503)"`, `"ashby: body over cap"`,
   `"ashby: published, unlisted"` on the success path); the reader's own
   steps are untouched.
5. **Terms.** Public, unauthenticated, documented for job-board
   consumption; recorded in the adapter module as its terms policy (v4
   §12). One GET per verification; no scheduled polling in this ticket.

## Out of scope

Discovery/scan of boards; `CLOSED` on absence; compensation
(`includeCompensation`); using `descriptionPlain` as the evaluation body;
other ATS adapters; a registry entry beyond the module (the capability
registry is its own Slice 2 item); any change to authority tiers, the
approval gate or the evaluator; extending `replay_reading`, which calls
the reader directly and never the Activity, so a replay does not exercise
the adapter.

## Known limit kept by this ticket

The evaluation body stays the pasted page. When that page has no visible
text and no JSON-LD posting, the workflow stops at `NEEDS_MORE_EVIDENCE`
before the model even though the API verified the posting as open. The
published path does not pre-render, so this also holds where the
reader's step-0 render could have supplied a body (not seen so far: the
observed postings on this ATS carry JSON-LD). The API's
`descriptionPlain` is the obvious body for such cases and is a
follow-up, not part of this ticket.

## Touch points

- `capabilities/ats/ashby.py`: URL parsing, lookup, response contract
  (only the fields used), terms note. `capabilities/http_fetch.py`: a
  `max_body_bytes` parameter with the current default.
- `contracts`: `ObservationHow.API`; `PageReadingRecord.adapter: str |
  None` ("not recorded" on older records).
- `activities/case_activities.py`: `verify_status` tries the adapter
  after the first fetch and before the reader; both branches store
  observations and classify exactly as today.
- Tests: unit (URL parsing incl. query/trailing segments; lookup against
  a fake transport: listed, unlisted, absent, 404 board, over-cap body,
  malformed JSON); Activity (listed → OPEN / OFFICIAL_ATS / ATS_LINK, no
  provider call, adapter field set; absent → reader path with the note);
  contract (old record decodes); the fake-network e2e for the listed case.
  README current scope and STATUS after reproduction.

## Acceptance and verification

- Published job (listed and unlisted alike): `OPEN`, authority
  `OFFICIAL_ATS`, apply entry `ATS_LINK` with the API's `applyUrl`, no
  model call during verification, reading marked `adapter: "ashby:
  published"` (or `"…, unlisted"`), both observations stored as
  snapshots.
- Published job whose page returns 404/410, or whose JSON-LD
  `validThrough` has passed: `CLOSED` / `EXPIRED` by the existing table,
  with the API entry recorded and rejected in the reason as today.
- Absent, board 404, oversized body, malformed JSON, transport error:
  the reader path runs, its result is unchanged, and the adapter field
  names the reason. A provider outage in that path still propagates as
  today.
- A URL on the API host, or a non-Ashby host, never enters the adapter.
- Old reading records without the field decode.
- Run and report `ruff check .`, `ruff format --check .`, `pyright`,
  `lint-imports`, `pytest -ra`, naming the disposable PostgreSQL database
  and counts; web checks only if the case page changes.
- Live: resubmit the URL of the earlier shell case (and one other
  posting on this ATS); verification reaches `OPEN` with no reader model
  call in the worker log, then the evaluator runs as usual. Real
  identifiers stay in private notes.

## Completion record

Implemented 2026-10-09 on top of `30b37b3`, committed as `d537802`
on 2026-10-10. Delivered as scoped: `capabilities/ats/ashby.py` (URL match,
one GET of the board, a response contract of the four fields used, the
terms note), `fetch_url(max_body_bytes=…)` with 8 MB for this call only,
`ObservationHow.API`, `PageReadingRecord.adapter`, and the
`verify_status` branch. A published posting, listed or unlisted, gets
the code-made reading of scope item 3, built from the reader's own
helpers for observation 0 and page dates; the existing decision table
then decides. Otherwise the reader runs unchanged and the reading
records the attempt. The known limit above was corrected in place: it
had said the workflow "still" stops, which is not true where a render
would have produced a body.

Choices made within the ticket:

- The URL matched is the fetched page's final URL, the one that also
  classifies observation 0: a link that redirects onto a posting on this
  ATS is looked up; a posting link that redirects off it is not.
- The API call is one fetch under the fetcher's usual 15-second limit,
  and its time comes out of the reading's 120 seconds, so the Activity's
  worst case stays what ADR-012 sized.
- The API observation is `OFFICIAL_ATS` only when the response comes
  from the API host; a redirect elsewhere is "unavailable". Its snapshot
  stores the board response as fetched, with source type `ats_api` and
  the adapter (`ats-adapter/ashby` `0.1.0`) as extractor.
- Posting fields are optional in the contract because the API omits
  missing data; a matched posting without an http(s) `applyUrl` is not
  verified by the adapter.
- Notes recorded: `ashby: published`, `ashby: published, unlisted`,
  `ashby: not published`, `ashby: published without an apply link`,
  `ashby: unavailable (http N)`, `ashby: unavailable (malformed
  response)`, `ashby: unavailable (redirected to <host>)` and `ashby:
  unavailable (<fetch error>)`. An oversized board reads `ashby:
  unavailable (declared content-length exceeds 8000000 bytes)` or
  `(response body exceeds 8000000 bytes)`, not the draft's "body over
  cap".

Review (`/code-review high`, nine findings). Fixed: observation 0 is
built by the reader's own `observe_response` instead of a copy of it; a
note is never left without its reason (an error without a message
records its type); a percent-encoded dot segment in the board is refused
like a literal one; a test pins the final-URL match in both directions.
Left as they are, for the owner to see: the published path does not
pre-render (the corrected known limit); the adapter fixes the API
observation at index 1 and its single caller places it second; each
verification stores the whole board, up to 8 MB, as a new blob when the
board's bytes changed, because the raw response is the observation; the
over-cap wording above. At closeout on 2026-10-10, the owner approved
adding `api.ashbyhq.com` to the existing ATS host list: pasted/followed
API URLs now classify as `OFFICIAL_ATS`, and company-host mappings
reject the shared host. This does not establish OPEN without apply
evidence or change the adapter's final-host check. The owner requested
the one-line change without additional tests; the existing suite was
rerun on the resulting commit.

Verification of the working tree, existing `.venv`, Python 3.14.3:
`ruff check .` and `ruff format --check .`: passed (formatter: 118
files); `pyright`: 0 errors; `lint-imports`: 3 contracts kept;
`pytest -ra`: **295 passed, no skips**, with disposable PostgreSQL
database `countersign_ashby_t003_124327` at `127.0.0.1:5435` (dropped
afterwards), SQLite fixtures elsewhere, Temporal's test server and local
Chrome. Network responses in the tests were faked and model providers
were fakes; nothing called the Ashby API. Web checks were not run: the
case page is unchanged. No commit, deployment or live model call was
made by that working-tree verification.

Commit verification (2026-10-10): `d537802` in a fresh detached
worktree and Python 3.14.3 venv. `.venv/bin/ruff check .` and
`.venv/bin/ruff format --check .`: passed (118 files);
`.venv/bin/pyright`: 0 errors; `.venv/bin/lint-imports`: 3 contracts kept;
`.venv/bin/pytest -ra`: **295 passed, no skips**, using disposable
PostgreSQL database `countersign_ashby_code_d537802` at `127.0.0.1:5435`
(dropped afterwards), SQLite fixtures elsewhere, Temporal's test server
and local Chrome. Private profile/mapping/salary inputs were unset;
external source responses and model providers were fakes.
`pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed,
with dependencies from `pnpm-lock.yaml`. These checks made no deployment
or live model calls. The live check below predates the final ATS-host
classification line; rebuild the worker to deploy that line.

Live check (2026-10-10, owner's rebuilt worker, direct API): four
postings on this ATS. Three, on boards of 24 and 157 postings (API
responses of 0.3 MB and 2.4 MB — the second already above the fetcher's
2 MB default, so the per-call cap mattered), verified `OPEN` /
`OFFICIAL_ATS` with the `ATS_LINK` entry from the API, no reader model
call, `adapter: "ashby: published"`, both observations stored; the
evaluator then ran as usual. The fourth sits on a board of about 3,000
postings (33 MB): the API attempt recorded `unavailable (response body
exceeds 8000000 bytes)`, the reader pre-rendered the shell and verified
`OPEN` from the rendered Apply control, as the fallback intends. Boards
above roughly 500 postings therefore take the reader path; raising the
cap is a judgment about memory and storage, not a bug.
