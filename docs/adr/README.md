# Architecture Decision Records

ADRs capture decisions that constrain implementation. They are append-only decision history: when a decision changes, add a superseding ADR rather than rewriting the original conclusion.

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](001-temporal-durable-workflow.md) | Temporal as the only durable workflow engine | Accepted |
| [ADR-002](002-postgresql-system-of-record.md) | PostgreSQL as the system of record | Accepted |
| [ADR-003](003-separate-three-kinds-of-truth.md) | Separate source facts, model assessments, and human decisions | Accepted |
| [ADR-004](004-central-capability-registry.md) | Govern external calls through a central capability registry | Accepted |
| [ADR-005](005-official-source-authority.md) | Official-source authority and stale-job policy | Accepted |

The implementation baseline is [Architecture v4](../job-search-agent-architecture-v4.md).

## ADR format

Each record contains:

- status and date;
- context and load-bearing constraint;
- decision;
- consequences and rejected alternatives;
- conditions that justify revisiting the decision.

