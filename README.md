# Countersign

A production-oriented, single-user agentic workflow system for collecting, verifying, evaluating, and approving external job intelligence.

The implementation baseline is [Architecture v4](docs/job-search-agent-architecture-v4.md). The project is currently in **Slice 1: one durable case**; runtime features described by the architecture are not claimed until their code, tests, and telemetry exist.

## Architecture at a glance

```text
Next.js Web → FastAPI API → Temporal Workflow → Python Activities
                     │                            │
                     └──── PostgreSQL ←──────────┘
                                                  ↓
                                      Capability Registry
                                       ├─ official sources
                                       └─ model provider
```

The codebase is a modular monolith with three eventual process entrypoints: `web`, `api`, and `worker`. PostgreSQL is the system of record; Temporal is the only durable workflow/background-job substrate.

## Repository map

```text
apps/web/                    Next.js control-plane UI
src/countersign/                Python modular monolith
evals/                       semantic eval cases and harness
infra/compose/               local/deployed Compose definitions (Slice 1)
migrations/                  Alembic migrations (Slice 1)
docs/adr/                    accepted architecture decisions
tests/                       unit, workflow, integration, E2E, fixture, security tests
```

Important import boundaries: `countersign.domain` may import only `countersign.contracts`; `countersign.workflows` may import only `countersign.contracts` and `countersign.domain`. I/O belongs in Activities. Both rules are enforced by import-linter.

## Decisions

- [ADR-001 — Temporal durable workflow](docs/adr/001-temporal-durable-workflow.md)
- [ADR-002 — PostgreSQL system of record](docs/adr/002-postgresql-system-of-record.md)
- [ADR-003 — Three kinds of truth](docs/adr/003-separate-three-kinds-of-truth.md)
- [ADR-004 — Central capability registry](docs/adr/004-central-capability-registry.md)
- [ADR-005 — Official-source authority](docs/adr/005-official-source-authority.md)

## Local checks

The web dependency graph is pinned by `pnpm-lock.yaml`. Python currently uses bounded dependency ranges in `pyproject.toml`; Slice 1 will generate and commit `uv.lock` when `uv` is adopted as the project runner.

```bash
uv sync --extra dev
uv run ruff check .
uv run pyright
uv run lint-imports
uv run pytest

pnpm install
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
```

## Running locally

```bash
docker compose -f infra/compose/docker-compose.yml up --build
```

This starts PostgreSQL (host port :5435), self-hosted Temporal (+ UI on
:8080), a one-shot Alembic migration, the API (:8000), the worker, and the
web UI (:3001).
The worker defaults to the deterministic fake model; set
`COUNTERSIGN_MODEL_PROVIDER=anthropic`, `COUNTERSIGN_MODEL_ID`, and
`ANTHROPIC_API_KEY` for live evaluations. Anthropic-compatible relays can set
`ANTHROPIC_BASE_URL`. Explicitly pass the repository-root environment file
when starting a live evaluation stack:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.yml up --build
```

Crash-recovery demo: create a case from the web UI, `docker compose kill
worker` mid-pipeline, `docker compose start worker`, and watch the case
resume with no duplicate snapshot, evaluation, or audit event.

## Current scope

Implemented (Slice 1 — one durable case):

- paste-a-URL intake with mandatory `Idempotency-Key`, 202 + durable workflow;
- `JobIntelligenceWorkflow`: snapshot → normalize → verify → evaluate →
  durable approval Signal wait, with quarantine/closed/expiry paths;
- SSRF-guarded fetch, deterministic HTML normalization, authority-aware
  official-status verification (never inferred from HTTP 200 alone);
- thin model gateway: structured output against the Pydantic contract,
  bounded schema-repair retry, token/latency/version capture; deterministic
  fake provider for tests and model-free runs;
- deterministic compensation signal computed from extracted salary data —
  never by the model;
- idempotent persistence (deterministic IDs, dedup constraints, converged
  retries) with append-only audit events in the same transaction;
- Alembic migrations, Compose core profile, CI (ruff, pyright,
  import-linter, pytest, web typecheck/build);
- workflow tests under Temporal's time-skipping environment, including an
  end-to-end run with real Activities and a duplicate-delivery test.

Not implemented yet (Slice 2+):

- watchlist + ATS adapters, scheduled scans and rechecks, capability
  registry policy enforcement, prompt/policy version registry;
- authorization, failure inbox UI, OTel traces and cost dashboard;
- golden evals, calibration, policy promotion.
