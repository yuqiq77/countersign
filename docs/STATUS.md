# Countersign — Status

Current focus, blockers, next steps, and owner decisions. Rewritten when
these change; history lives in git and ADRs. Private live-run details stay
in the gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-09-25 |
| Branch | `main`, no remote. Reader, browser packaging and attestation: `73191d7`; required apply entry (`page-reader/0.3.0`), persisted reasons/notes and replay diagnostic: `da3c056`. Documentation is committed separately. |
| Slice | Slice 1 (one durable case): code complete; exit milestone (Track A) observed 2026-09-18; worker-kill recovery repeated 2026-09-19. |
| Current focus | Complete the live regression set after the rebuilt deployment: two company-page cases reached pending approval on 2026-09-25; navigation and attested approval remain to verify. |

## Current implementation

- **Reader (ADR-012):** one loop inside `verify_status`, with DONE, FOLLOW
  of an observed link, and RENDER of the current page. Each observation
  exposes at most **120 controls**, deduplicated by target (by label for
  target-less buttons), in document order; `controls_truncated` marks a
  cut. Bounds are **120 seconds** after the initial fetch, three model
  steps, two follows and one schema repair per step. Reader prompt:
  `page-reader/0.3.0`; the Activity's outer timeout remains three minutes.
  The reading's apply entry is a **required object with an explicit
  `NONE` kind**: with an optional object, live models on both routes
  described a working Apply control in their notes and left the field
  null, stably across three models; the same stored page was cited
  exactly once the choice had to be made. The model's reason for each
  step and its final notes are persisted with the reading;
  `python -m countersign.diagnostics.replay_reading <case or snapshot id>`
  replays a stored observation without refetching.
- **Pre-render:** when the first page has **no visible HTML text and no
  controls**, code renders once before the model and records step 0.
  This also applies when JSON-LD already supplies the posting body. The
  model still interprets the result; no keyword or candidate-ranking
  fallback manufactures an apply entry. Provider outages retry the Activity
  rather than persist an outage-derived verdict.
- **Evidence and provenance:** control labels and targets are checked
  together; claims on superseded pages must also hold on their replacement.
  Pages are classified independently: official first, then closed/expired,
  then the verified apply entry, then the latest observation. Verification
  pins the governing source; evaluation, salary-source stamping, approval
  pinning and case detail use the selected posting body. Snapshot identity
  includes URLs, HTTP status and content.
- **Browser packaging:** the Dockerfile installs Chromium through Playwright
  and Compose uses the image's fixed browser path. Host runs supply
  `COUNTERSIGN_CHROMIUM_PATH` or run without rendering. Rendering permits
  only the vetted page host and referenced subdomains, GET/HEAD only;
  service workers and WebSockets are blocked. Synchronous DNS/library
  calls are not forcibly cancelled by the reading budget.
- **Verification and approval (ADR-013):** `UNVERIFIED` replaces
  `UNKNOWN_STALE_RISK`; evaluator prompt is `evaluator/0.2.1`. Plain APPROVE
  requires verified OPEN. On UNVERIFIED or OPEN_THIRD_PARTY_ONLY, a reviewer
  can approve with a typed attestation and a note of what they observed.
  The API checks the verification pinned to the request; CLOSED, EXPIRED
  and a missing verification cannot be attested. The decision and
  `human.verification_attested` audit event are stored together. The UI
  explains verification status and separates attested approval; model
  confidence remains in the contract but is no longer displayed.
- **Schema:** unreleased migration `0001` includes `page_readings`,
  verification `reading_id`, attestation fields and the revised snapshot
  identity. The enum rename also affects stored payloads and Temporal
  histories. Upgrading a stack from before ADR-013 requires fresh
  schema/history. The subsequent required-entry and diagnostics change
  adds no migration; the rebuilt worker runs `page-reader/0.3.0`.

## Verification and deployment

Code commit `da3c056` was checked on 2026-09-25 in a fresh detached
worktree and Python 3.14.3 venv, with front-end dependencies installed
from `pnpm-lock.yaml`:

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  102 Python files; `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest`: **157 passed, no skips**. PostgreSQL integration used
  disposable database `countersign_code_check` at `127.0.0.1:61661` in a
  separate temporary container. Other database fixtures used SQLite;
  Temporal used its test server; rendering used real local Chromium.
  Private profile and salary environment inputs were unset for this run.
- `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed.
- Changed and new files were checked for operator-specific identifiers,
  credential-like values and private paths; no matches. Private files
  remain ignored and untracked. A full gitleaks scan remains a publication
  prerequisite.

The required-entry fix was first checked on 2026-09-22 by replaying a
stored company-page observation. That diagnostic reproduced the prior
null-entry failure and accepted the exact control/target under the new
schema; it was not an end-to-end deployment run.

**Live deployment evidence, 2026-09-25:** after the owner rebuilt the
application containers, two real company-careers cases ran through fetch,
`page-reader/0.3.0`, `evaluator/0.2.1` and approval creation. Read-only
PostgreSQL inspection confirmed both reached `PENDING_APPROVAL`, each with
two snapshots, one reading, one evaluation and one approval. Each reading
used one static HTTP 200 observation and ended DONE on step 1, with an
accepted `APPLY_CONTROL`, zero rejected claims, and persisted `why` and
`notes`. Verification was `OPEN_THIRD_PARTY_ONLY` / `COMPANY_CONTROLLED`;
plain APPROVE remained gated, and approval verification/evaluation pins
matched. These are fresh live workflow runs, not diagnostic replays.
Neither run exercised FOLLOW, RENDER or human attestation; both approvals
were still pending when inspected.

The rebuilt worker reports `page-reader/0.3.0`; a browser-launch smoke
check inside it successfully started packaged Chromium (153.0.8010.12).
The earlier base-image fetch timeout is no longer the deployment blocker.
No new live model calls or deployment changes were made during this
verification/commit round. Earlier runs retain recovery and provider-error
retry evidence; private case details stay outside tracked documentation.

## Next

1. Complete the rebuilt deployment's live regression set: direct ATS,
   board → ATS, a client-rendered shell (step-0 RENDER), and one attested
   approval. The two company-page runs and container browser launch are
   verified; they do not cover these remaining paths.
2. Before publication, run gitleaks and inspect tracked content. The owner
   adds the remote and pushes.
3. Slice 2 retains the v4 §24 timebox: policy watchlist and official-domain
   mapping, ATS adapters, scans and stale rechecks, capability/route
   registries, calibration, traces and cost dashboard. Tickets are
   owner-read before implementation. Slice 2.5 research follows ADR-011
   once registry prerequisites exist.

## Remaining gaps and owner decisions

- Authority classification is unchanged: company pages require official
  domain mapping; several boards still fall into COMPANY_CONTROLLED and
  TRUSTED_BOARD_REDIRECT is not assigned. Whether board evidence alone
  permits an unattested APPROVE remains a separate policy decision.
- Cross-host script dependencies can prevent complete rendering. Citation
  checks establish provenance, not semantic correctness; the reader still
  needs the live regression set above.
- Attestation is visible in the decision row and audit log, not yet on the
  case page. Case-state reasons for quarantine/needs-evidence remain in the
  audit log; verification reasons are shown in the UI.
- Rubric decisions remain: no-profile score nullability, hard blockers and
  decision bands, pay-level risk wording, technical versus coordination
  work. Usage outliers and relay cache accounting remain in `HANDOFF.md`.
- Owner-reserved v4 edits: §9 needs-evidence edge; §12 / §14 old status name;
  §12 / §15 reader; §15 / §24 / §29 research loop when Slice 2.5 nears.
