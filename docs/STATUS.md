# Countersign — Status

Current focus, blockers, next steps, and what waits on the owner. Rewritten
in place when any of those change; not a changelog (history: `git log`,
`docs/adr/`; rules: `AGENTS.md`).

| Field | Value |
| --- | --- |
| As of | 2026-09-19 |
| Branch | `main`, no remote yet. Track A runtime fixes: `a80ac12`; formatting and CI gate: `89730d4`. |
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
- **Open extraction gap found in the rerun:** one Ashby vacancy's static
  HTML had no visible posting text; its content lived in a JSON-LD
  `JobPosting` script block. The current normalizer skips scripts, so
  verification returned `UNKNOWN_STALE_RISK` and the gate withheld
  `APPROVE`. The model received an empty posting and returned `SKIP` with
  low confidence, consuming a call without enough source evidence.
  Scope JSON-LD extraction and a pre-model evidence sufficiency check in
  Slice 2; decide adapter coverage, the state transition, and any policy
  threshold when that work is approved.
- The live stack includes the committed runtime fixes. Formatting changes
  no application behavior; another recreate is not a pending step.

## Next

1. Make the repository public: run gitleaks once, inspect the tree for
   private identifiers (see `AGENTS.md` constraints), and have the owner
   add the remote and push.
2. **Slice 2 — governed intelligence** (timebox 3 weeks, kill rules in v4
   §24): watchlist as a policy artifact including company → official-domain
   mapping (this is what unlocks `OFFICIAL_COMPANY`); Greenhouse + Lever
   adapters; scan-report import; scheduled scans + stale rechecks;
   capability-registry policy enforcement; gateway route registry; anchor
   calibration cards; OTel traces + cost dashboard. Triage the extraction
   and evidence-sufficiency gap above when scoping tickets. Exit = Claim
   Level 1 (§25) + scheduled scans running. Tickets under `docs/tickets/`
   start here: one per workstream, cut and owner-read before code.
3. **Slice 2.5 — company-research loop** per ADR-011, after Slice 2
   (registry enforcement and the gateway route registry are prerequisites).
   Kill rule: the `CompanyProfile` schema ships regardless; an
   operator-filled profile is the degraded mode.
4. v4 §15 / §24 / §29 do not yet mention the research loop. Propose that
   edit when Slice 2.5 nears (owner-reserved).

## Waiting on the owner

- **System prompt enrichment.** The evaluator's system prompt is about
  1.3k characters and carries no operator profile, so personalized fit
  judgments lack that evidence. Operator facts belong in
  `config/operator/` (gitignored) and reach the prompt as policy values,
  never as tracked text. Prompt caching requires a model-dependent minimum
  prefix (1024 tokens on Sonnet 4.6, 512 on Opus 5); the current system
  prompt is estimated at ~330 tokens. Stable-prefix caching has not been
  observed in the live runs (`cache_creation_input_tokens: 0`).
- **Usage numbers through the relay are unstable.** One run reported
  25,732 input tokens for a prompt estimated at ~3.9k (system + posting
  text + output schema); another reported 99 for a request whose system
  prompt alone is estimated at ~330 tokens. Verify with direct calls
  (`count_tokens` and one real request) before the Slice 2 cost dashboard
  trusts `usage`, and decide whether cost accounting must bypass the relay.

## Recently done (pointers only)

Slice 1 commit series + ADR-011 (`git log b87cb11..44f54da`); delivery and
working-agreement updates (`git log 44f54da..a79236c`); Track A follow-up
code and tests (`a80ac12`), deployment verification, and the repeated
worker-kill recovery check (2026-09-19); formatting cleanup and CI gate
(`89730d4`).
