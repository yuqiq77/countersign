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

The web dependency graph is pinned by `pnpm-lock.yaml`. Python runtime
dependencies use bounded version ranges in `pyproject.toml`; there is no
Python lock file yet. Pinning the image's dependency graph is a v4 §21
item scheduled with the deployed image (Slice 5). The commands below are
the ones CI runs. `pytest` skips the PostgreSQL channel unless
`COUNTERSIGN_TEST_POSTGRES_URL` points at a disposable database (see
`tests/integration/test_postgres.py`).

The development formatter, Ruff, is pinned in `pyproject.toml` so local
formatting and CI use the same version. Run `.venv/bin/ruff format .` to
apply formatting before checking it.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/lint-imports
.venv/bin/pytest

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
web UI (:3001). All host ports bind to 127.0.0.1.
The worker defaults to the deterministic fake model; set
`COUNTERSIGN_MODEL_PROVIDER=anthropic`, `COUNTERSIGN_MODEL_ID`, and
`ANTHROPIC_API_KEY` for live evaluations. Anthropic-compatible relays can set
`ANTHROPIC_BASE_URL`. The unscripted fake reader leaves status unknown.
Rendering uses Playwright and a Chromium binary named by
`COUNTERSIGN_CHROMIUM_PATH`; the Compose image installs one and sets the
path itself, host runs name their own or run without rendering.
The evaluator judges fit against an operator profile
only when `COUNTERSIGN_OPERATOR_PROFILE_PATH` names one — a gitignored
Markdown file under `config/operator/`, given as a repository-relative path;
Compose mounts that directory read-only at the same relative location in the
worker. Unset, the prompt states that no candidate profile was provided and
the model must not infer one. Explicitly pass the repository-root environment
file when starting a live evaluation stack:

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
  durable approval Signal wait, with quarantine/closed/expiry paths and a
  needs-evidence stop before fit evaluation when the reader found no
  posting text;
- SSRF-guarded fetch (each hop pinned to its vetted address, with session
  cookies kept per fetch under the hop's real hostname and never in the
  shared client), deterministic HTML normalization (visible text, or
  the JSON-LD `JobPosting` description when a client-rendered page has
  none), and a bounded page reader in front of verification (ADR-012): the
  model reads a dossier of page facts and may render the page or follow a
  listed link, within code-enforced budgets; cited controls must match
  their labels and targets, and status uses one observation's own HTTP
  response, authority and evidence. Model failures leave no inferred apply
  entry; HTTP 200 alone never proves OPEN. Chromium rendering records
  actual navigation metadata;
- a code-enforced approval gate: a plain APPROVE needs a verified official
  OPEN; on an unverified posting the reviewer may approve only with a typed
  attestation of what they saw, re-checked by the API and audited as its
  own human event (ADR-013);
- an evidence loop: "add context and re-evaluate" records the reviewer's
  note as their decision and continues the same workflow into the next
  round, which re-verifies the posting and re-evaluates with the note as
  labelled operator context, up to three rounds. Each later round also
  receives the preceding approval's pinned assessment as a revisable
  judgment; earlier evaluations remain available in case detail (ADR-014);
- thin model gateway: structured output against the Pydantic contract,
  bounded schema-repair retry, token/latency/version capture; deterministic
  fake provider for tests and model-free runs;
- deterministic compensation signal computed from extracted salary data
  against acceptable and attractive thresholds: `CONCERNING`, `ACCEPTABLE`,
  or `ATTRACTIVE`; missing data or a currency mismatch yields `UNKNOWN`;
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
