# Development

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
