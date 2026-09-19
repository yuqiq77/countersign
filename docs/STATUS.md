# Countersign — Status

Current focus, blockers, next steps, and what waits on the owner. Rewritten
in place when any of those change; not a changelog (history: `git log`,
`docs/adr/`; rules: `AGENTS.md`).

| Field | Value |
| --- | --- |
| As of | 2026-09-20 |
| Branch | `main`, no remote yet. Salary-policy configuration, version identity, and source stamping committed in `0ec1882`; not yet deployed. |
| Slice | Slice 1 (one durable case): code complete; exit milestone (Track A) observed 2026-09-18, worker-kill recovery repeated 2026-09-19. |

## Now

- **Track A follow-up implemented and committed (`a80ac12`):** blank
  `ANTHROPIC_BASE_URL` values are cleared before SDK construction, keeping
  its argument → environment → CLI profile → default chain intact. The
  provider cache includes the environment-derived base URL. Normalization
  prefers `og:title` (extractor `1.1.0`); worker logging distinguishes
  activity start from provider invocation, and the accepted response's
  request id is logged and persisted as `ModelUsage.provider_request_id`.
  Compose host ports bind to loopback; the web list waits for its first
  successful load before showing "No cases yet." Configuration and title
  regressions are covered by tests.
- **Formatting cleanup committed (`89730d4`):** Ruff 0.16.1 reformatted
  20 Python files with no AST changes from the pre-format sources. The
  development dependency is pinned to that version, and CI includes
  `ruff format --check .`. README and AGENTS list the same check.
- **Formatting-commit verification, 2026-09-19:** fresh detached worktree
  and venv at `89730d4` (Python 3.14.3, reviewed dependency versions).
  `ruff check .` clean · `ruff format --check .` reports all 85 files
  formatted · `pyright` 0 errors · `lint-imports` 3/3 kept · `pytest`
  85 passed with `COUNTERSIGN_TEST_POSTGRES_URL` pointing at the disposable
  `countersign_format_commit` database in a separate `postgres:16-alpine`
  container on `127.0.0.1:59156`. Other database fixtures use isolated
  SQLite databases. `pnpm --dir apps/web typecheck` and
  `pnpm --dir apps/web build` both passed after a frozen-lockfile install.
  The live Compose stack was unchanged.
- **Compose has been recreated; current deployment checked 2026-09-19:**
  PostgreSQL `:5435`, Temporal `:7233`, Temporal UI `:8080`, API `:8000`,
  and web `:3001` all publish on `127.0.0.1`. API `/healthz`, web, and
  Temporal UI returned HTTP 200. The running worker's seven changed Python
  modules matched `a80ac12` byte-for-byte, including the
  provider-request-id changes. Future updates should recreate the whole
  stack with `docker compose --env-file .env -f
  infra/compose/docker-compose.yml up --build -d`: the earlier service-only
  update recreated Temporal but left its UI using the old connection.
- **Recovery evidence:** the original 2026-09-18 Greenhouse run reached
  `PENDING_APPROVAL` after worker termination during evaluation and a
  180 s StartToClose timeout. At that point it had 1 evaluation, 1 approval
  pinning snapshot / verification / evaluation IDs, 8 audit events, and
  2 snapshots (`pasted_url` + `verification_observation`), with no duplicate
  artifacts. The raw responses differed in `<title>` while their
  normalized text hashes matched, so `content_changed=false`.
  The owner repeated the kill test on a Lever vacancy on 2026-09-19 using
  an earlier image of the follow-up set. Its logs recorded evaluation
  attempt 1 before termination and successful evaluation after restart.
  Read-only checks of PostgreSQL and Temporal history confirm attempt 2
  after a StartToClose timeout, `PENDING_APPROVAL` at case version 7,
  1 evaluation, 1 approval, 7 audit events, and 2 snapshots. The two earlier
  cases are APPROVED and REJECTED, with completed workflows and 8 audit
  events each. These historical evaluations have no provider request id;
  the start line alone does not prove that attempt 1 reached the model.
  Request-id capture is now deployed; it does not backfill prior runs.
  **First run on the recreated stack (2026-09-19, a Lever vacancy, no
  kill):** the worker log shows the full chain — activity start
  (attempt 1) → `provider request` → HTTP 200 → `provider response` with a
  `req_…` id → activity finish with the same id and usage — and the id is
  persisted in the evaluation payload and returned by the API. Every
  activity ran at attempt 1; 8 audit events; approval pins consistent;
  `APPROVE` allowed (OFFICIAL_ATS + verified OPEN).
  **Direct-API session (2026-09-19):** the owner switched
  the worker to the official endpoint and re-ran the two Lever vacancies
  plus one new Greenhouse vacancy. Uncached `input_tokens` for the two
  reruns matched the relay runs exactly (1,265 and 2,456), so the relay
  had reported usage faithfully. Prompt caching is now observed, not
  inferred: the first direct request wrote a 2,847-token prefix and the
  next two — different postings — read exactly 2,847, so the cached prefix
  is the posting-independent part (system prompt + the structured-output
  schema the API places ahead of the messages; the relay's 2,860 differed
  by 13 tokens). The new vacancy's first evaluate attempt got a genuine
  HTTP 500 (`api_error`, request id logged); the SDK made no retry of its
  own (one POST — consistent with the server's `x-should-retry: false`),
  Temporal's RetryPolicy started attempt 2 one second later and it
  succeeded. One evaluation row, 7 audit events, no duplicates. Decisions
  reproduced across providers: the same postings returned `APPLY_NOW` at
  confidence 0.82/0.82 and 0.82/0.80.
- **Open extraction gap found in the rerun:** one Ashby vacancy's static
  HTML had no visible posting text; its content lived in a JSON-LD
  `JobPosting` script block. The current normalizer skips scripts, so
  verification returned `UNKNOWN_STALE_RISK` and the gate withheld
  `APPROVE`. The model received an empty posting and returned `SKIP` with
  low confidence, consuming a call without enough source evidence.
  Scope JSON-LD extraction and a pre-model evidence sufficiency check in
  Slice 2; decide adapter coverage, the state transition, and any policy
  threshold when that work is approved.
- **Salary-policy fixes committed (`0ec1882`), not yet deployed:** Compose
  now passes the four `COUNTERSIGN_SALARY_*` variables from the private
  `.env` to the worker. Blank counts as unset; all four unset uses the
  generic example with a startup warning. A partial set or invalid amounts
  stops the worker before it connects. Currency is normalized; thresholds
  must be positive and strictly ascending. Shared `countersign.env`
  helpers preserve the existing provider configuration behavior.
  An 8-hex fingerprint of the normalized bands enters `policy_version`
  (`default-rubric/0.1.0+bands.<hash>`), so different bands produce distinct
  evaluation IDs through the existing version dimension. When verification
  is present, code overwrites the salary extraction's `source_authority`
  and `official` fields from that authority; amount, currency, and citation
  extraction remain model-assisted.
  The deployment gap was missing operator configuration, not disabled
  compensation logic: the observed EUR extractions correctly returned
  `UNKNOWN` against USD example bands. Currency mismatches and missing
  salary data still return `UNKNOWN`; this change adds no currency
  conversion and does not promise a classified signal for those postings.
- **Implementation-commit verification, 2026-09-20:** fresh detached
  worktree and venv at `0ec1882`, Python 3.14.3, dependencies constrained to
  the reviewed local versions. `ruff check .` clean; `ruff format --check .`
  reports 87 files formatted; `pyright` 0 errors; `lint-imports` 3/3 kept;
  `pytest` **93 passed, none skipped** with `COUNTERSIGN_TEST_POSTGRES_URL`
  set to the disposable `countersign_salary_code` database in a separate
  `postgres:16-alpine` container on `127.0.0.1:63270`. Other database
  fixtures use isolated SQLite databases. `pnpm install --frozen-lockfile`,
  `pnpm --dir apps/web typecheck`, and `pnpm --dir apps/web build` passed.
  Read-only `docker compose --env-file .env.example -f
  infra/compose/docker-compose.yml config --format json` checks confirmed
  blank and synthetic complete salary-variable passthrough. A worker
  subprocess with partial synthetic configuration exited 2 before
  connecting. No model calls or live-stack changes were made.
- **Deployment remains pending:** set all four private salary variables
  in `.env` and recreate the whole stack with the command above when ready.
  Existing evaluations are not rewritten or automatically re-evaluated.

## Next

1. Keep profile loading as the next independent implementation change;
   the private profile is prepared, but the evaluator does not yet read it
   (scope proposal below).
2. Make the repository public: run gitleaks once, inspect the tree for
   private identifiers (see `AGENTS.md` constraints), and have the owner
   add the remote and push.
3. **Slice 2 — governed intelligence** (timebox 3 weeks, kill rules in v4
   §24): watchlist as a policy artifact including company → official-domain
   mapping (this is what unlocks `OFFICIAL_COMPANY`); Greenhouse + Lever
   adapters; scan-report import; scheduled scans + stale rechecks;
   capability-registry policy enforcement; gateway route registry; anchor
   calibration cards; OTel traces + cost dashboard. Triage the extraction
   and evidence-sufficiency gap above when scoping tickets. Exit = Claim
   Level 1 (§25) + scheduled scans running. Tickets under `docs/tickets/`
   start here: one per workstream, cut and owner-read before code.
4. **Slice 2.5 — company-research loop** per ADR-011, after Slice 2
   (registry enforcement and the gateway route registry are prerequisites).
   Kill rule: the `CompanyProfile` schema ships regardless; an
   operator-filled profile is the degraded mode.
5. v4 §15 / §24 / §29 do not yet mention the research loop. Propose that
   edit when Slice 2.5 nears (owner-reserved).

## Waiting on the owner

- **Private-profile loading — separate follow-up, not implemented.**
  An operator-authored profile exists in `config/operator/profile.md`
  (gitignored); its contents and real policy values stay out of tracked
  files. The current evaluator still receives no candidate profile, so
  preparing the file alone does not fix personalized fit judgments.
  The observed unsupported engineering-profile claim and `REALISTIC`
  seniority remain a regression case for the over-enthusiasm trap.
  The minimal proposal is to load a private profile into the stable system
  prefix, validate required profile input before live evaluation, and bind
  profile revisions to evaluation identity. Loader configuration and
  version representation are not yet implemented. A missing-profile mode
  that still forces candidate-specific scores has not been adopted;
  nullable scores and approval-gate changes are outside this salary fix.
  Preserve the distinction between career preferences, demonstrated
  evidence, and job requirements when the prompt is updated.
- **Usage accounting: one outlier left.** The relay's numbers turned out
  faithful (identical uncached counts to the direct API for identical
  postings), and the "99 input tokens" reading was a misread here —
  `input_tokens` is the uncached remainder; that run had 2,860 cached
  tokens beside it, and an empty posting body explains the small
  remainder. The first run's 25,732 with zero cache activity remains
  unexplained and has not recurred; the relay stays in use alongside the
  direct API for cross-checks during development, so watch for it. For
  the Slice 2 cost dashboard: cost = `input_tokens` + cache writes at the
  write premium + cache reads at the read rate + `output_tokens`; never
  the single field.

## Recently done (pointers only)

Slice 1 commit series + ADR-011 (`git log b87cb11..44f54da`); delivery and
working-agreement updates (`git log 44f54da..a79236c`); Track A follow-up
code and tests (`a80ac12`), deployment verification, and the repeated
worker-kill recovery check (2026-09-19); formatting cleanup and CI gate
(`89730d4`); salary-policy fixes and isolated implementation-commit
verification (`0ec1882`, 2026-09-20).
