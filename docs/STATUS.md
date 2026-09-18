# Countersign — Status

Current focus, blockers, next steps, and what waits on the owner. Rewritten
in place when any of those change; not a changelog (history: `git log`,
`docs/adr/`; rules: `AGENTS.md`).

| Field | Value |
| --- | --- |
| As of | 2026-09-18 |
| Branch | `main`, 14 commits, working tree clean, no remote yet. |
| Slice | Slice 1 (one durable case): code complete; exit milestone not yet run. |

## Now

- **Slice 1 is committed as eight layered commits** (domain → persistence →
  fetch → evaluation → workflow → api → web → delivery) plus ADR-011. Each
  Python commit was verified in isolation when the series was made (fresh
  worktree + venv: ruff, import-linter, full pytest at that point).
- **Verification of the current tree, 2026-09-17, port shift applied:**
  `ruff check .` clean · `pyright` 0 errors · `lint-imports` 3/3 kept ·
  `pytest` 77 passed, 1 skipped (SQLite channel; the skip is the Postgres
  test) · `tests/integration/test_postgres.py` 1 passed against a
  disposable `postgres:16-alpine` container on `127.0.0.1:5499` · web
  `next typegen && tsc --noEmit` clean. Not re-run: `next build`.
- **Compose boot reproduced, 2026-09-18:** PostgreSQL healthy on host
  `:5435`; Alembic migration exited 0; Temporal cluster reported `SERVING`
  after explicitly setting its container-internal `DB_PORT=5432`; the
  application worker remained up; API `/healthz`, web `:3001`, and Temporal
  UI `:8080` responded successfully. The live-provider worker was then
  recreated with `--env-file .env`; provider, model ID, API key, and relay
  base URL were all present (presence-only checks; no values printed).
- **Track A milestone is the open item** (v4 §24, Slice 1 exit): boot
  Compose, paste one real vacancy on a known ATS host (Greenhouse, Lever,
  Ashby, SmartRecruiters — a company-controlled page stalls at
  `OPEN_THIRD_PARTY_ONLY` by design until Slice 2), kill and restart the
  worker mid-pipeline, confirm no duplicate snapshot, evaluation, or audit
  event. README's "Current scope" already lists Slice 1 as implemented;
  this run is what turns that claim from asserted into observed.
- Nothing is In Progress.

## Next

1. Track A milestone (above). Delete any `countersign.db` or Compose
   `pgdata` volume created before the final `0001` schema first.
2. Make the repository public: run gitleaks once, grep the tree for private
   identifiers (see `AGENTS.md` constraints), add the remote, push.
3. **Slice 2 — governed intelligence** (timebox 3 weeks, kill rules in v4
   §24): watchlist as a policy artifact including company → official-domain
   mapping (this is what unlocks `OFFICIAL_COMPANY`); Greenhouse + Lever
   adapters; scan-report import; scheduled scans + stale rechecks;
   capability-registry policy enforcement; gateway route registry; anchor
   calibration cards; OTel traces + cost dashboard. Exit = Claim Level 1
   (§25) + scheduled scans running. Tickets under `docs/tickets/` start
   here: one per workstream, cut and owner-read before code.
4. **Slice 2.5 — company-research loop** per ADR-011, after Slice 2
   (registry enforcement and the gateway route registry are prerequisites).
   Kill rule: the `CompanyProfile` schema ships regardless; an
   operator-filled profile is the degraded mode.
5. v4 §15 / §24 / §29 do not yet mention the research loop. Propose that
   edit when Slice 2.5 nears (owner-reserved).

## Waiting on the owner

- Nothing at the moment.

## Recently done (pointers only)

Slice 1 commit series + ADR-011 (2026-08-29; `git log b87cb11..44f54da`);
MIT `LICENSE`, the port shift, the Temporal `DB_PORT` fix, the working
agreement (`AGENTS.md`, `CLAUDE.md`, this file), and the README "Local
checks" rewrite (four commits, 2026-09-18).
