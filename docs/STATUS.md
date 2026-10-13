# Countersign — Status

Current state and next actions, not a changelog. Implementation details
live in [README](../README.md#current-scope), decisions in [ADRs](adr/README.md),
and historical verification in git. Private live-run details stay in the
gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-10-11 |
| Branch | `scheduled-watch` (from `main` at `b15472c`), no remote; ticket 004 code: `a15cc23`, with documentation in the following commit. Ashby adapter: `d537802`. |
| Slice | Slice 1 implementation and core live paths verified; release/tag decision remains with the owner. |
| Current focus | Ticket 004 (scheduled watch, ADR-016 accepted) committed and verified locally; two review findings remain, and the Schedule and live acceptance have not yet been run. |

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
- **Scheduled watch ([ticket 004](tickets/004-scheduled-watch.md),
  ADR-016):** a private watchlist
  (`COUNTERSIGN_WATCHLIST_PATH`) drives one Temporal Schedule. Each run
  rechecks PENDING_APPROVAL cases under a model-call budget, then lists
  the watched Ashby/Greenhouse boards and admits filtered postings from
  a lookback window as posting-derived cases under a second budget.
  Rechecks record without a state transition and skip the model on an
  unchanged page; a closure (decision table, or Ashby board absence on
  two consecutive rechecks) expires the approval via a `source_closed`
  Signal that shares the decision's slot. No schema change. Not yet run
  on a real Temporal server or live.
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

Ticket 004 code commit `a15cc23` was checked on 2026-10-11 in a fresh
detached worktree and Python 3.14.3 venv: ruff check and format (129
files), pyright 0 errors, lint-imports 3 contracts kept, `pytest -ra`
**348 passed, no skips** on disposable PostgreSQL database
`countersign_watch_code_a15cc23` at `127.0.0.1:5435` (dropped), and web
typecheck/build with dependencies installed from `pnpm-lock.yaml`.
Source responses and model providers were fakes; no Schedule was created
on any server. Details are in the ticket.

Code commit `d537802` was checked on 2026-10-10 in a fresh detached
worktree and Python 3.14.3 venv: `pytest -ra` **295 passed, no skips** on
disposable PostgreSQL database `countersign_ashby_code_d537802` (dropped),
with the other local checks passing. Details are in ticket 003.

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

1. Follow up the two watch review findings below. The owner writes the
   private watchlist, rebuilds the worker and confirms the
   Schedule exists, then runs the two-week live acceptance in the
   ticket. Watch whether Ashby moves `publishedAt` on republication.
2. Owner decides the Slice 1 release boundary and any further live
   coverage above. Before publication: full gitleaks/private-identifier
   sweep, release tag and v4 §25 claim review. The owner adds the remote
   and pushes; no tag or publication is implied by this commit series.
3. The worker rebuild for ticket 004 also deploys ticket 003's final
   ATS-host classification line.
4. Then the scan-report import, registries, calibration (including
   score noise), traces and cost, within the v4 §24 timebox. Research
   remains Slice 2.5 under ADR-011.

## Known limits and deferred decisions

- Host normalization uses Python's built-in IDNA, which differs from the
  HTTP client for some names (such as `ß`): a Unicode entry can miss its
  intended punycode host or grant authority to a different ASCII spelling.
  Accepted/deferred by the owner on 2026-09-28; use verified ASCII/punycode
  entries for now. A mapped main host covers all its paths; apply-entry
  checks do not eliminate reader misinterpretation.
- Watch: Greenhouse has no verify-path adapter, so a Greenhouse posting
  taken down closes through the decision table or approval expiry, not
  board absence; hosted URLs of boards with their own careers site
  redirect to the company page, official only when mapped. A candidate
  left over by the budget can age out of the window. Rechecks of Ashby
  cases each read the whole board. The Schedule code has no automated
  test (the test server has no Schedules).
- Two watch review findings remain in the committed implementation:
  the pending-request check and recheck write lack concurrent-decision
  protection, so an old round's recheck can become the next round's
  pinned verification; Ashby secondary-location address locality/country
  fields are not extracted, so a matching secondary location can be
  filtered out. The owner requested committing with only the retry-budget
  finding fixed; these two remain follow-ups, not verified guarantees.
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
