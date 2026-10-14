# Ticket 003 — Ashby posting API as the official ATS observation

- **Status:** closed. Code `d537802` (2026-10-10); the final ATS-host line
  shipped with the ticket 004 worker rebuild.
- **Basis:** [ADR-005](../adr/005-official-source-authority.md) (an official
  ATS API is tier 1), [ADR-012](../adr/012-bounded-page-reader.md) (the
  adapter is a code-initiated observation), v4 §§11–12, 15.

## Summary

Ashby postings are client-rendered shells: the static page carries only
JSON-LD, and the apply control appears only after a render. Ashby also
publishes a public, unauthenticated JSON API per job board, documented for
job-board use. When a pasted page is an Ashby posting, verification now
reads that API once. A published posting with an apply link is verified
OPEN from the API without a model call; anything else falls back to the
page reader unchanged.

## What was built

- **Match.** The fetched page's final URL, `jobs.ashbyhq.com/<board>/<job-id>`.
- **Lookup.** One GET of the board's posting API through the SSRF-guarded
  fetcher, inside the reading's time budget. The API has no per-job
  endpoint, so this call alone may read up to 8 MB; a larger board counts
  as unavailable rather than being parsed in part.
- **Published.** A matching posting with an http(s) `applyUrl` gets a
  code-made reading: the page as observation 0, the API response as an
  `OFFICIAL_ATS` observation 1, and an `ATS_LINK` apply entry. `isListed`
  is recorded, not judged: unlisted means reachable by direct link, which a
  pasted link is. The decision table is unchanged, so the page's own
  404/410 or past `validThrough` still wins. The evaluator runs as before.
- **Anything else.** Absent, unavailable, oversized, malformed or
  redirected responses leave the reading to the reader, and the reading
  records why (for example `ashby: not published`).
- **ATS host list.** At closeout, `api.ashbyhq.com` joined the list, so a
  pasted API URL classifies as `OFFICIAL_ATS` and cannot be mapped as a
  company host. This line has no dedicated test; the suite was rerun on
  the commit.

Out of scope, and since handled by [ticket 004](004-scheduled-watch.md):
discovery and closure on board absence. Still out of scope: compensation,
other ATS adapters, and replaying the adapter.

## Verification

- **Commit `d537802`**, fresh worktree and venv (Python 3.14.3): ruff,
  pyright and import-linter passed; `pytest -ra` **295 passed, no skips**
  on a disposable PostgreSQL database; web typecheck and build passed.
  Network responses and model providers were fakes.
- **Live, 2026-10-10:** four postings. Three, on boards of 24 and 157
  postings (0.3 and 2.4 MB), verified OPEN / `OFFICIAL_ATS` from the API
  with no reader model call. The fourth sits on a board of about 3,000
  postings (33 MB): the API attempt was recorded as unavailable, and the
  reader verified OPEN from the rendered Apply control. Boards above
  roughly 500 postings therefore take the reader path.
- **Review**: nine findings; the fixes went into the commit, and the open
  ones are listed below.

## Remaining issues

- The published path does not pre-render: a page with no text and no
  JSON-LD stops at NEEDS_MORE_EVIDENCE although the API verified it OPEN.
  Using the API's `descriptionPlain` as the body is the follow-up.
- Each verification stores the whole board response, up to 8 MB, as a new
  blob whenever the board changed.
- The adapter fixes the API observation at index 1, and replay does not
  exercise the adapter.

The full working record (draft scope, touch points, review detail and the
working-tree run) is in git at `1bb2a68`.
