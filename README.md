# Countersign

A production-oriented workflow system for one narrow agentic task: checking
whether a job posting is still open and worth applying to, and leaving a
record a human can sign off on.

The model reads and judges, code decides what counts as verified, and a
human makes the call. Each stage runs as a durable workflow step with
idempotent effects and an append-only audit trail, so a result can be
traced to the page, the rule, the model call and the human decision behind
it.

The implementation baseline is [Architecture v4](docs/job-search-agent-architecture-v4.md). The project is currently in **Slice 1: one durable case**; runtime features described by the architecture are not claimed until their code, tests, and telemetry exist. [Current scope](#current-scope) lists what is implemented.

## Why this exists

It started with a small, specific failure. A general-purpose agent running
a scheduled job search kept returning stale postings and links that had
already gone 404. The answers carried links but no evidence: nothing showed
when a posting had last been seen open, or whether anything had checked. A
better prompt would have made the answers more convincing, not more
checkable.

Making them checkable is a systems problem rather than a prompting one:

- **Status comes from rules in code, applied to what the page shows.** A
  404 or 410 is CLOSED, a past `validThrough` is EXPIRED, and OPEN needs a
  cited apply entry on an official source; anywhere else the same entry
  reaches only OPEN_THIRD_PARTY_ONLY. HTTP 200 alone proves nothing
  ([decision table](src/countersign/domain/verification.py)).
- **External calls fail.** Fetches and model calls retry as durable
  workflow steps, and a worker killed mid-step resumes without writing a
  second snapshot, evaluation or audit event.
- **The decision belongs to a human** and can arrive days later. The
  approval request waits as durable workflow state and pins the snapshot,
  verification and evaluation it was issued for.
- **Model output is attributable.** Readings and evaluations are stored
  with the prompt version, model, token counts and latency of the calls
  behind them; evaluations also carry the policy version.

## A production system, not a personal agent

Personal agents are built for breadth: one assistant for many tasks, with
the answer as the product. Countersign goes the other way. It runs one task
through a fixed pipeline, and the record is the product:

```text
URL → snapshot → page reader → status → evaluation → approval
      code       model         code     model        human
```

The model's autonomy is bounded on purpose. The only loop it drives is the
page reader's choice to render the page or follow a listed link, inside one
Activity and under step budgets enforced in code
([ADR-012](docs/adr/012-bounded-page-reader.md)); its claims count only
after citation checks. Besides approving or rejecting, the reviewer can add
context and send the case through another round of verification and
evaluation, up to three rounds in all
([ADR-014](docs/adr/014-evidence-loop-through-request-evidence.md)).

The question behind the project is not how to build a better job-search
assistant, but what it takes to make an agentic workflow recoverable,
reviewable, attributable and auditable.

## Why Temporal for one user

Temporal is heavy for a single-user tool, and it is here on purpose. The
problems it solves already exist at n=1: fetches and model calls fail and
must be retried without repeating their effects, an approval can wait up
to seven days, and a worker can die mid-step. Without it, the same
guarantees would have to be rebuilt from cron jobs, status columns and
hand-written retry and timer code.

The costs are accepted and written down: workflow code must stay
deterministic (the import boundary that keeps I/O in Activities is enforced
by import-linter), a Temporal service has to run, and idempotency still
belongs to the application because Activities run at least once.
[ADR-001](docs/adr/001-temporal-durable-workflow.md) records the decision
and the conditions for revisiting it.

## What it does not demonstrate

It has one operator and a small, real workload, so it says nothing about
scale (traffic, concurrency, horizontal scaling), multi-user security or
team engineering process. Self-hosted Temporal on one host shows recovery
from a worker crash, not host failover. Planned pieces that are not built
yet, including authorization, traces, cost dashboards and evals, are
listed under [Current scope](#current-scope).

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
- [ADR-011 — Bounded company-research loop](docs/adr/011-bounded-company-research-loop.md)
- [ADR-012 — Bounded page reader](docs/adr/012-bounded-page-reader.md)
- [ADR-013 — Human attestation for unverified postings](docs/adr/013-human-attestation-for-unverified-postings.md)
- [ADR-014 — Evidence loop through REQUEST_EVIDENCE](docs/adr/014-evidence-loop-through-request-evidence.md)
- [ADR-015 — Render network policy](docs/adr/015-render-network-policy.md)
- [ADR-016 — Scheduled watch](docs/adr/016-scheduled-watch.md)

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

Implemented (Slice 1 core plus Slice 2 official-host mapping, the
Ashby adapter and the scheduled watch):

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
  listed link, with one citation repair inside the same code-enforced
  step budget and structured rejection records; cited controls must match
  their labels and targets, and status uses one observation's own HTTP
  response, authority and evidence. Without a completed reading there is
  no inferred apply entry; HTTP 200 alone never proves OPEN. Chromium
  rendering records actual navigation metadata;
- separate system-recommendation and human-approval gates: the system's
  APPLY_NOW framing needs verified official OPEN; a pending approval
  permits APPROVE when its pinned verification is not CLOSED/EXPIRED.
  Approval without official OPEN is recorded as the reviewer's own
  verification with its own audit event (ADR-013, amended);
- explicit official company hosts: a private JSON mapping of exact
  hostnames the operator declares official, loaded by path like the
  profile and validated at worker start; a matched host is
  OFFICIAL_COMPANY and can reach OPEN with a cited apply entry. No
  wildcards, path rules or inference, boards stay boards, and each reading
  records the mapping version and fingerprint with every observation's
  authority; a retried verification returns the stored result. IDNA
  normalization can differ from the HTTP client; use verified ASCII or
  punycode entries (see [known limits](docs/STATUS.md#known-limits-and-deferred-decisions));
- an Ashby adapter in verification ([ticket 003](docs/tickets/003-ashby-adapter.md)):
  a posting page on Ashby is looked up once in its board's public
  posting API. A posting the API publishes with an apply link is verified
  from that OFFICIAL_ATS response without a model call, while the page's
  own 404/410 or past `validThrough` still decides; an absent posting,
  an unavailable, oversized or malformed response leaves the reading to
  the page reader, and the reading records the outcome. Absence alone
  never closes a posting at verification;
- a scheduled watch ([ticket 004](docs/tickets/004-scheduled-watch.md),
  ADR-016), implemented and tested but not yet run live: one Temporal
  Schedule from a private watchlist file runs a workflow that rechecks
  cases waiting for a decision, then reads the watched Ashby and
  Greenhouse boards through their public APIs. A recheck runs the verify
  path again without a state transition; it skips the model when the page
  text and its cited claims are unchanged. When the decision table finds
  the posting closed or expired, or an Ashby posting is absent from its
  board with no JobPosting on the page on two consecutive rechecks, the
  case's approval is expired with that reason. New postings that pass a
  location and title filter and fall within a lookback window become
  cases, newest first, under a per-run budget, and go through the same
  pipeline and human approval. Each run records one audit event.
  Relevance stays with the evaluator; nothing is reopened;
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
  against annual acceptable and attractive thresholds: `CONCERNING`,
  `ACCEPTABLE`, or `ATTRACTIVE`; a monthly figure is annualised by twelve;
  missing data, a currency mismatch, or an hourly or unstated pay period
  yields `UNKNOWN`;
- idempotent persistence (deterministic IDs, dedup constraints, converged
  retries) with append-only audit events in the same transaction;
- Alembic migrations, Compose core profile, CI (ruff, pyright,
  import-linter, pytest, web typecheck/build);
- workflow tests under Temporal's time-skipping environment, including an
  end-to-end run with real Activities and a duplicate-delivery test.

Not implemented yet (Slice 2+):

- further ATS adapters (Lever, SmartRecruiters), importing external scan
  reports, rechecks of approved cases, capability registry policy
  enforcement, prompt/policy version registry;
- authorization, failure inbox UI, OTel traces and cost dashboard;
- golden evals, calibration, policy promotion.
