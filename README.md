# Countersign

A production-oriented, single-user agentic workflow system for collecting, verifying, evaluating, and approving external job intelligence.

The implementation baseline is [Architecture v4](docs/job-search-agent-architecture-v4.md). The project is currently in **Slice 0: contracts and skeleton**; runtime features described by the architecture are not claimed until their code, tests, and telemetry exist.

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

## Current scope

Implemented in this skeleton:

- accepted ADR-001 through ADR-005;
- Python and web package boundaries;
- strict base contracts and canonical source/status enums;
- minimal FastAPI health endpoint;
- import-linter rules expressing the deterministic workflow boundary;
- test/eval/infrastructure directory ownership.

Not implemented yet:

- the Job Intelligence Workflow or Activities;
- persistence models and migrations;
- external source adapters and model calls;
- authorization, audit, observability, or deployed infrastructure.
