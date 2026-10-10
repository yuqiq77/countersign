# Countersign — Status

Current state and next actions, not a changelog. Implementation details
live in [README](../README.md#current-scope), decisions in [ADRs](adr/README.md),
and historical verification in git. Private live-run details stay in the
gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-10-10 |
| Branch | `main`, no remote; Ashby adapter: `d537802`. Documentation committed separately. |
| Slice | Slice 1 implementation and core live paths verified; release/tag decision remains with the owner. |
| Current focus | Ticket 003 (Ashby adapter) implemented, reviewed and verified; live API and reader-fallback checks recorded. The final ATS-host classification line awaits worker rebuild. |

## Current state

- **Reader `page-reader/0.5.0` (ADR-012/015):** bounded FOLLOW/RENDER,
  source-named JSON-LD fields, underscore-tolerant citations, one citation
  repair within the existing step budget, structured rejected claims and
  backward-readable records. Source checks, authority rules and navigation
  permissions are unchanged. Pydantic minimum is 2.11.
- **Official hosts ([ticket 002](tickets/002-official-domain-mapping.md)):**
  private JSON via `COUNTERSIGN_OFFICIAL_HOSTS_PATH`, validated at worker
  start; exact hosts only, no new navigation permissions. A match grants
  `OFFICIAL_COMPANY`, not `OPEN` without a cited apply entry. Readings
  retain policy version/fingerprint and per-observation authority;
  posting consumers use the body observation, with a snapshot fallback
  for legacy records. Retries return persisted results and body length;
  unreadable/invalid policy fails without retries. Replay uses only an
  explicit `--policy`. No schema change; unset preserves prior behavior.
- **Ashby adapter ([ticket 003](tickets/003-ashby-adapter.md)):**
  a page landing on an Ashby posting is looked up once in its board's
  public posting API (8 MB cap for that call, inside the reading's time
  budget). A published posting gets a code-made reading, the page as
  observation 0 and body, the API response as OFFICIAL_ATS observation 1
  with the apply link, and the existing table decides, so a 404/410 or
  past `validThrough` on the page still wins. Anything else runs the
  reader as before and records the attempt on the reading. No schema
  change; replay does not exercise the adapter. The API host is also in
  the shared ATS host list, so generic observations classify consistently
  and company-host mappings cannot register it as a company host.
- **Fetch/render:** per-fetch redirect cookies use logical hostnames; the
  shared client's jar is inert. Rendering permits page-named resources
  under the existing DNS-pinned, read-only policy.
- **Evidence loop and approval (ADR-013/014):** at most three rounds under
  one workflow id; each approval pins its evidence. Previous assessments
  are revisable context; the UI retains round history and server-owned
  in-flight state. Under the ADR-013 amendment, APPROVE needs a pinned
  verification that is not CLOSED/EXPIRED, with no extra checkbox or note.
  Approval without official OPEN is recorded server-side with its own
  audit event. The model's APPLY_NOW framing still needs official OPEN.
- **Evaluator `evaluator/0.3.2`:** preserves stated salary amounts and
  period; monthly ×12 for annual-band comparison, hourly/unstated periods
  UNKNOWN. Stored signals are not recomputed. No migration is added.

## Verification and deployment

Code commit `d537802` was checked on 2026-10-10 in a fresh detached
worktree and Python 3.14.3 venv, with front-end dependencies installed
from `pnpm-lock.yaml`:

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  118 files reported by the formatter; `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest -ra`: **295 passed, no skips**, using disposable
  PostgreSQL database `countersign_ashby_code_d537802` at
  `127.0.0.1:5435` (dropped afterwards), SQLite fixtures, Temporal's test
  server and local Chrome. Private profile/mapping/salary inputs were
  unset; model providers and external source responses were fakes.
- `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed.

Ticket 003 records the owner's 2026-10-10 live check: three postings used
the API path without a reader model call; a fourth exceeded the API body
cap and reached OPEN through the reader's render fallback. The final
ATS-host classification line was added after that check and still needs
a worker rebuild. Commit verification did not deploy or call live models.

The owner reset the business database and Temporal history on 2026-09-27
(business export retained privately, old Temporal history not retained).
The mapped/unmapped live check passed on 2026-09-28: the same company
page went from `OPEN_THIRD_PARTY_ONLY` to `OPEN` / `OFFICIAL_COMPANY`,
with policy identity on the reading. After the owner's rebuild, read-only
`docker compose exec worker` inspection confirmed the final overlapping-retry
and unreadable-file fixes and a loaded mapping. A `psql` read-only transaction
against the Compose `countersign` business database confirmed simplified
approval live: one `APPROVE` pinned to `OPEN_THIRD_PARTY_ONLY`, request
`DECIDED`, case `APPROVED`, `approved_without_official_open=true`, and both
`approval.approve` and `human.verification_attested` audit events present.
These checks neither changed business data nor invoked a model.

## Live evidence and remaining coverage

Previously observed: crash recovery/provider retry; static, FOLLOW and
pre-render paths; the evidence loop and round pager; attested approval;
salary mentioned only in a reviewer note staying out of extraction.

The pre-reset regression set, retained in a private business-data export,
confirmed:

- a board link follows through a cookie session to the application form,
  remains OPEN_THIRD_PARTY_ONLY and reaches approval without quarantine;
- the previously rejected board posting verifies in round 1 via its Apply
  control, with no rejected claims;
- a monthly salary records MONTHLY and ACCEPTABLE;
- a second evidence round pins the preceding evaluation and answers the
  reviewer's question.

Still **not observed live**: JSON_LD_DIRECT_APPLY as the selected entry
(the model preferred the Apply control), the citation-repair step firing,
and CLOSED reached through a followed page. Automated checks cover these
rules; the successful board run does not prove those specific paths.

## Next

1. Owner decides the Slice 1 release boundary and any further live
   coverage above. Before publication: full gitleaks/private-identifier
   sweep, release tag and v4 §25 claim review. The owner adds the remote
   and pushes; no tag or publication is implied by this commit series.
2. Rebuild the worker for ticket 003's final ATS-host classification
   line. The adapter's API and oversized-board fallback paths have
   recorded live checks; approval rules are unchanged.
3. Then scans/rechecks, registries, calibration (including score noise),
   traces and cost, within the v4 §24 timebox. Research remains Slice 2.5
   under ADR-011.

## Known limits and deferred decisions

- Host normalization uses Python's built-in IDNA, which differs from the
  HTTP client for some names (such as `ß`): a Unicode entry can miss its
  intended punycode host or grant authority to a different ASCII spelling.
  Accepted/deferred by the owner on 2026-09-28; use verified ASCII/punycode
  entries for now. A mapped main host covers all its paths; apply-entry
  checks do not eliminate reader misinterpretation.
- Ashby adapter: the published path does not pre-render, so a posting
  page with no text and no JSON-LD stops at NEEDS_MORE_EVIDENCE although
  the API verified it OPEN (the API description as body is a follow-up).
- Host-only cookies also reach subdomains under the current cookiejar
  policy; accepted for now. A main-document redirect to a named resource
  host can send one GET before the renderer rejects the final host
  (ADR-015); no observation is returned.
- A JS button without an href cannot be FOLLOWed. Some cross-host
  dependencies still prevent rendering; blocking DNS/library calls can
  outlast the reader budget. Citation checks establish provenance, not
  semantic correctness.
- Approvals without an official OPEN and quarantine reasons remain in audit
  records rather than dedicated case-page UI. Score movement of ±1 has been
  observed; it is not treated as evidence of a meaningful change.
- **Route observation, no new fix:** a relay run failed output-shape
  validation; `hard_blockers`/risk grouping also differed across routes.
  Different schema handling is a hypothesis, not an established cause,
  and schema conformance does not establish semantic correctness. The
  owner defers prompt/contract changes unless this becomes a material,
  recurring problem.
- Owner decisions remain on rubric/blocker wording, no-profile scoring,
  official-domain/board policy, public claims and v4 updates for the
  reader/evidence loop. Structured context effects, stricter cookie
  matching, evaluator replay and generic JS navigation stay deferred.
