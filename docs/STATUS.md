# Countersign — Status

Current state and next actions, not a changelog. Implementation details
live in [README](../README.md#current-scope), decisions in [ADRs](adr/README.md),
and historical verification in git. Private live-run details stay in the
gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-09-27 |
| Branch | `main`, no remote; approval simplification: `f02b2af`. Documentation committed separately. |
| Slice | Slice 1 implementation and core live paths verified; release/tag decision remains with the owner. |
| Current focus | Approval simplification implemented and deployed; live acceptance pending. Ticket 002 remains a local Slice 2 draft for owner review. |

## Current state

- **Reader `page-reader/0.5.0` (ADR-012/015):** bounded FOLLOW/RENDER,
  source-named JSON-LD fields, underscore-tolerant citations, one citation
  repair within the existing step budget, structured rejected claims and
  backward-readable records. Source checks, authority rules and navigation
  permissions are unchanged. Pydantic minimum is 2.11.
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

Code commit `f02b2af` was checked on 2026-09-27 in a fresh detached
worktree and Python 3.14.3 venv; front-end dependencies came from
`pnpm-lock.yaml`:

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  109 Python files; `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest -ra`: **221 passed, no skips**. PostgreSQL integration
  used disposable database `countersign_approval_code_f02b2af` at
  `127.0.0.1:5435` (dropped afterwards). Other database fixtures used
  SQLite; Temporal used its test server and rendering used local Chrome.
  Private profile/salary inputs were unset; model providers were fakes.
- `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed.

The owner reset the business database and Temporal history and rebuilt on
2026-09-27. Read-only checks confirmed the new
`approved_without_official_open` column, removal of the old decision
columns and Signal fields, and the worker's derived-flag logic. Both
stores were empty at inspection. Business records were exported privately;
old Temporal history was not retained. The simplified approval flow has
not yet been observed live after this cutover. This commit verification
did not deploy or make live model calls.

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
2. Confirm the simplified approval flow live, then review local draft
   Ticket 002 for company → official-host mapping. Mapping implementation
   has not started. The owner chose Ashby as the first ATS adapter, using
   existing cases for validation, ahead of v4's Greenhouse-first sequence.
   Mapping changes source authority, not human approval rules.
3. Then scans/rechecks, registries, calibration (including score noise),
   traces and cost, within the v4 §24 timebox. Research remains Slice 2.5
   under ADR-011.

## Known limits and deferred decisions

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
