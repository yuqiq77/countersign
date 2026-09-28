# Ticket 002 — Recognize explicitly configured official company hosts

- Status: complete, 2026-09-28; code `a6ae8db`, documentation committed
  separately. Final retry/file-read fixes await deployment.
- Slice: first Slice 2 ticket; the authority portion of the watchlist
  (v4 §11 item 2, "a versioned policy artifact").
- Basis: [v4 §§11–12, 17, 24](../job-search-agent-architecture-v4.md),
  [ADR-005](../adr/005-official-source-authority.md). Approval rules
  (ADR-013) are unchanged by this ticket. The owner approved explicit
  company-host policy; this ticket does not amend accepted ADRs or v4.

## Goal

A posting on a host the operator has explicitly declared official reaches
`OFFICIAL_COMPANY`, and `OPEN` only when the existing reader and
verification rules establish an accepting application entry on that
observation. Before this ticket, `classify_authority()` recognized known
ATS and discovery hosts and left every other host at `COMPANY_CONTROLLED`,
so a company's own careers page could never be `OPEN`.

What this ticket does not change, stated so the live expectation is honest:

- A shared job board stays a board, even when it names a mapped employer
  or links to it. Board postings remain `OPEN_THIRD_PARTY_ONLY`; the
  trusted-board tier (ADR-005, `TRUSTED_BOARD_REDIRECT`) is a separate
  policy decision.
- An application-system vendor's shared host is not a company entry.
  Vendor hosts go through the code's ATS list with a test per vendor,
  not through this mapping.

## Scope

Four pieces, delivered together: load the mapping, classify with it, keep
the trace in existing JSON records, replay only on explicit request.

1. **Load the mapping.** A JSON file named by one environment variable
   (`COUNTERSIGN_OFFICIAL_HOSTS_PATH`), loaded the way the operator
   profile is: no implicit default path, unset means no mapping and the
   current behaviour, a set path that is missing, unreadable, empty or
   malformed makes the worker refuse to start. The file declares a
   `version` string and a list of entries, each a hostname and a company
   key. Hostnames are normalized (lower-case, trailing dot removed, no
   scheme or path) before duplicate detection; two entries for one
   normalized host fail validation. The company key is an audit label
   only: it introduces no company entity, relation or deduplication. The
   loader computes an 8-hex sha256 fingerprint of the file's content, the
   same identity scheme as the profile. Public examples use synthetic
   companies on reserved example domains.
2. **Classify.** Exact match of the observation's normalized final-URL
   host against the mapping. No wildcards, no registrable-domain guesses,
   no path rules, no company-name matching and no model-authored entries;
   subdomains are listed explicitly. v1 matches on host only: registering
   a company's main host is the owner's explicit acknowledgement of that
   host's source authority, not a statement that every page on it is
   open. The apply-entry requirement limits what a mapped host can turn
   into `OPEN`, but the reader can still misread an ordinary page; that
   remains an accepted boundary, not a removed risk. Every observation is
   classified by its own final URL, including FOLLOW and RENDER results,
   so a mapped start URL confers nothing on an unmapped redirect
   destination, and an unmapped start URL that redirects or follows to a
   mapped host is classified by where it landed. The mapping is validated
   at worker start and loaded once per Activity attempt into the closure
   that classifies all observations of that attempt; a later, unfinished
   attempt may use a newer mapping. Mapping does not permit an extra
   fetch, widen reader or render allow-lists, or bypass SSRF controls.
3. **Keep the trace in existing JSON records; no schema change.** Each
   observation reference in the page-reading record carries the
   authority it received in that attempt and the matched entry's company
   key (or none); the record carries the mapping's declared `version`
   and content fingerprint. Records written before these fields exist
   read back as "not recorded", never as an explicit "no mapping".
   Consumers take authority from the observation they use: the
   verification from its governing observation as today, and the
   evaluator's official-salary stamp from the reading's body
   observation, not from the stored snapshot's `authority_level` (a
   snapshot is reused when content is unchanged, so its stamp may predate
   the mapping) and not from the governing verification (the posting
   body and the application page can be different sources). A reading
   written before the field exists falls back to the body snapshot's
   `authority_level`, which is what the evaluator uses today; `None`
   never reaches the evaluator. The reading record also keeps the
   normalized length of its body observation's text, which the retry
   path below needs. The mapping file's private Git history is the
   retained revision; no lookup service for historical versions is
   built.
4. **Replay on explicit request only.** `countersign.diagnostics.replay_reading`
   gains `--policy <path>`; without it no mapping is loaded, whatever the
   worker's environment says. The output names the version and
   fingerprint it used. Replay is an experiment that re-reads stored
   content with the current reader; stored records remain the history.

## Consistency checks with a code basis

Both are acceptance points of this ticket.

- **A retried `verify_status` returns the stored result.** Previously the
  Activity read and classified afresh on every attempt, while
  `record_page_reading` and `record_verification` keep the first row
  written under an id; a retry after the commit but before Temporal saw
  the completion would hand the workflow a verification the database does
  not hold. The Activity checks for its `verification_id` first and
  returns the stored record when it exists, and reads it back after commit
  in case another overlapping attempt won. `VerifyOutcome.observed_text_chars`
  must be restored with it: `None` takes the workflow's old-history path
  and skips the empty-body stop, so the value comes from the stored
  reading's body text length and, for a reading written before that
  field, from re-normalizing the body snapshot's blob. No mapping
  fingerprint enters the workflow input.
- **Authority per observation survives snapshot reuse.** With the same
  content stored before and after a mapping change, the new reading's
  observation carries the new authority and the evaluator's salary stamp
  follows it; the old reading and its approval pins are untouched.

## Out of scope

Company identity, relations or deduplication; cross-retry policy pinning;
historical policy lookup; path prefixes or wildcards; the trusted-board
tier; ATS adapters; scheduled scans and rechecks; automatic official-source
discovery; a mapping-management UI; new MODEL capabilities; evaluator
prompt changes; approval-rule changes;
schema changes; historical backfill or automatic reruns of existing cases.
The existing server-owned human-approval gate remains unchanged.

## Expected implementation touch points

- `contracts/`: the mapping contract; observation-reference and
  page-reading record fields with defaults that read as "not recorded".
- `policy/official_hosts.py`: loader, normalization, validation,
  fingerprint, mirroring `policy/operator_profile.py`.
- `capabilities/normalize.py`: classification takes the mapping as an
  explicit input; the ATS and discovery lists stay in code.
- `activities/case_activities.py`: intake snapshot stamp, `verify_status`
  (mapping closure, stored-result return, per-observation trace),
  `evaluate_case` (authority from the reading's body observation).
- `diagnostics/replay_reading.py`: `--policy`.
- Compose: mount the file read-only next to the profile; `env.py` if a
  new variable needs the blank-means-unset rule.
- Tests: unit (loader, normalization, classification), repository and
  PostgreSQL (record fields, old-record decoding), API (case detail shows
  the observation authority), workflow (idempotent retry). README current
  scope and STATUS only after the implementation is reproduced.

## Acceptance and verification

- Start with a failing test: a synthetic company page on a mapped host
  with a valid cited Apply control is `OFFICIAL_COMPANY` and `OPEN`; the
  same page unmapped stays `OPEN_THIRD_PARTY_ONLY`.
- Reject suffix look-alikes and unlisted subdomains; accept a differently
  cased host and a trailing dot; redirects and follows in both directions
  classify by the landing host; an ATS source is classified exactly as
  before; a board page carrying the mapped employer's name or an official
  link, without an observed official application entry, stays a board.
- Mapping plus HTTP 200 is not `OPEN`. No-entry, CLOSED and EXPIRED keep
  their semantics; plain APPROVE still uses the server-owned gate and the
  verification pinned to its request.
- Loader: unset variable preserves current behaviour; missing, empty,
  malformed and duplicate-host files fail at worker start; the fingerprint
  is stable for identical content and changes with it.
- Records: old readings without the fields decode; a new reading shows
  per-observation authority, matched key, mapping version and
  fingerprint; the evaluator's salary stamp follows the body observation.
- Both consistency checks above, including a retry after the commit of
  an empty-body reading (no text, no JSON-LD posting) that still returns
  `observed_text_chars == 0` and stops the workflow before the model, and
  a retry of a non-empty one that returns the first attempt's length.
- Replay: without `--policy` an unmapped classification, with it the
  mapped one, and the output states which policy it used; no test loads
  a real operator file.
- Run and report `ruff check .`, `ruff format --check .`, `pyright`,
  `lint-imports`, `pytest -ra`, `pnpm --dir apps/web typecheck` and
  `pnpm --dir apps/web build`, naming the disposable PostgreSQL database
  and the actual pass/skip counts. If commits are requested, verify every
  layer in a fresh worktree and venv; separate code and docs commits.
- Before claiming live coverage: one owner-confirmed company page reaches
  official `OPEN` while an unmapped or board control stays below official
  `OPEN` (the system's `APPLY_NOW` gate, not human `APPROVE`), with
  real hosts and case identifiers kept in private notes. Fake-provider
  tests do not stand in for this.

## Completion record

Implemented and reviewed through 2026-09-28, code commit `a6ae8db`.
Delivered as scoped: `contracts/authority_policy.py`,
`policy/official_hosts.py`, `classify_authority(url, official_hosts)` with
`host_of` / `normalize_host` / `is_shared_host`, per-observation
`authority` and `official_host_entry` plus `authority_policy_version`,
`authority_policy_fingerprint` and `body_text_chars` on the reading
record, `repo.get_page_reading` and `repo.body_observation`,
the stored-result return in `verify_status`, the reading-derived
authority in `evaluate_case` and as `CaseDetail.posting_authority` (the
snapshot record keeps its retrieval-time stamp untouched), `replay_reading
--policy`, Compose and `.env.example` wiring, and the synthetic example
`config/official-hosts.example.json`. Also fixed while there: a stored
verification read from SQLite came back with a naive `checked_at`; it is
now timezone-aware like every other stored datetime, so the retried
result equals the first.

Review findings addressed before commit: entries are validated as bare
hostname labels, so a wildcard like `*.example` is refused rather than stored as a literal
that never matches; a mapping file with no entries is refused; the
fingerprint hashes the stripped text, as the profile's does; a mapping
that stops parsing after worker start fails the Activity as non-retryable
`PolicyError` before any fetch, instead of four retries and an opaque
quarantine; one repository helper (`body_observation`) resolves the body
snapshot and its reference together, so a reading's authority is never
stamped onto a fallback snapshot it did not classify and the reading is
loaded once per request. Two owner-found edges after that: the entry
check only covered an attempt that had already committed, so a timed-out
attempt still running could commit between the retry's check and its
writes, leaving the database with one status and the workflow with
another (reproduced by interleaving two attempts on a disposable
PostgreSQL database) — `verify_status` now reads the verification and
text length back after its commit and returns what the database holds;
and an unreadable mapping file raised `OSError` past the loader's
`ValueError` contract, so Temporal retried instead of failing as
`PolicyError` — the loader now converts it.

Verification of `a6ae8db` in a fresh detached worktree and Python 3.14.3
venv, with front-end dependencies from `pnpm-lock.yaml`:
`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`: passed
(formatter: 112 files); `.venv/bin/pyright`: 0 errors;
`.venv/bin/lint-imports`: 3 contracts kept;
`.venv/bin/pytest -ra`: **259 passed, no skips**, using SQLite fixtures and
disposable PostgreSQL database `countersign_hosts_code_a6ae8db` at
`127.0.0.1:5435` (dropped afterwards), Temporal's test server and local
Chrome; no real operator file loaded by any test; model providers were
fakes. `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`:
passed. No deployment or live model calls were made by these checks.

Live check (2026-09-28, owner's deployment; persisted results confirmed
read-only at closeout): the same company careers page was
`OPEN_THIRD_PARTY_ONLY` / `COMPANY_CONTROLLED` without a mapping, with
null policy fields. Mapped, it was `OPEN` / `OFFICIAL_COMPANY` on the same
cited mailto entry, with policy version/fingerprint and body-observation
authority recorded. This verifies source authority, not that evaluation
is generally unaffected: official-salary provenance uses that authority.
The deployed worker predates the final overlapping-retry and unreadable-file
fixes; rebuild before relying on them. Simplified approval live acceptance
is a separate pending check.

Known limits accepted by the owner at closeout: Python's built-in IDNA
and the HTTP client differ for some Unicode hostnames (for example `ß`).
A Unicode mapping entry can miss the intended punycode host or classify
a different ASCII spelling as official; normalization on both sides does
not fix this discrepancy. Use verified ASCII/punycode entries for now;
alignment is deferred. Host-only granularity covers every page on a
listed main host, bounded but not made infallible by the apply-entry
requirement. Older readings are not backfilled.
