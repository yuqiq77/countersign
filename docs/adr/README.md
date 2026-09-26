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
| [ADR-012](012-bounded-page-reader.md) | Bounded page reader in front of verification: the model chooses render/follow/done inside code-enforced budgets; code binds citations to observed controls and sources, decides status, and degrades conservatively | Accepted |
| [ADR-013](013-human-attestation-for-unverified-postings.md) | `UNVERIFIED` replaces `UNKNOWN_STALE_RISK`; a reviewer may approve an unverified posting only with a typed attestation, re-checked by the API against the pinned verification and audited as its own human event | Accepted |
| [ADR-014](014-evidence-loop-through-request-evidence.md) | The reason of a `REQUEST_EVIDENCE` decision is the operator-supplied context; the decision starts the next generation (re-verify, re-evaluate with the context attributed, new approval), bounded to three rounds | Accepted |
| [ADR-015](015-render-network-policy.md) | Render network policy: the main document stays on the page host and its subdomains; resources may load from any host the fetched HTML names, DNS-pinned and read-only, without domain guesses or vendor lists | Accepted |

The implementation baseline is [Architecture v4](../job-search-agent-architecture-v4.md).

## ADR format

Each record contains:

- status and date;
- context and load-bearing constraint;
- decision;
- consequences and rejected alternatives;
- conditions that justify revisiting the decision.

