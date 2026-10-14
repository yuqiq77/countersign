# Architecture Decision Records

ADRs capture decisions that constrain implementation. They are append-only decision history: when a decision changes, add a superseding ADR rather than rewriting the original conclusion.

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](001-temporal-durable-workflow.md) | Temporal as the only durable workflow engine | Accepted |
| [ADR-002](002-postgresql-system-of-record.md) | PostgreSQL as the system of record | Accepted |
| [ADR-003](003-separate-three-kinds-of-truth.md) | Separate source facts, model assessments, and human decisions | Accepted |
| [ADR-004](004-central-capability-registry.md) | Govern external calls through a central capability registry | Accepted |
| [ADR-005](005-official-source-authority.md) | Official-source authority and stale-job policy | Accepted |
| [ADR-011](011-bounded-company-research-loop.md) | Bounded company-research loop activates model-invoked capabilities | Accepted |
| [ADR-012](012-bounded-page-reader.md) | A bounded page reader in front of verification | Accepted |
| [ADR-013](013-human-attestation-for-unverified-postings.md) | Approving a posting the system did not verify is the reviewer's own verification | Accepted, amended 2026-09-27 |
| [ADR-014](014-evidence-loop-through-request-evidence.md) | Reviewer context starts a new round, up to three | Accepted |
| [ADR-015](015-render-network-policy.md) | Render network policy: navigation versus resource hosts | Accepted |
| [ADR-016](016-scheduled-watch.md) | Scheduled watch: board discovery and rechecks of pending cases | Accepted |

The implementation baseline is [Architecture v4](../job-search-agent-architecture-v4.md).

## ADR format

Each record contains:

- status and date;
- context and load-bearing constraint;
- decision;
- consequences and rejected alternatives;
- conditions that justify revisiting the decision.

