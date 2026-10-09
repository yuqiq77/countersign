# Ticket 003 — Ashby posting API as the official ATS observation

- Status: draft for owner review; not approved for implementation.
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
text and no JSON-LD posting, the workflow still stops at
`NEEDS_MORE_EVIDENCE` before the model even though the API verified the
posting as open; the API's `descriptionPlain` is the obvious body for
such cases and is a follow-up, not part of this ticket.

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

Not implemented.
