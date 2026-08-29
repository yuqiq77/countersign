# ADR-002: PostgreSQL as the System of Record

- Status: Accepted
- Date: 2026-07-18
- Owners: Project maintainer
- Supersedes: None
- Related: [Architecture v4 §§8, 21–22](../job-search-agent-architecture-v4.md#8-domain-model)

## Context

The product must preserve canonical job cases, immutable source observations, model runs, approvals, versioned policies, audit events, idempotency records, and an outbox. These records have transactional relationships and must survive retries and concurrent workflow/API writes.

Semantic retrieval is useful only for selected evidence and ambiguous duplicate proposals. It is not an authoritative representation of a case or its history. At the expected single-user volume, a separate vector database or object store would add deployment and consistency work before it solves a measured problem.

## Decision

PostgreSQL is the application system of record from the first slice.

- SQLAlchemy maps persistence models; Pydantic contracts remain separate from ORM models.
- Alembic owns all application schema migrations.
- State transitions and their audit events commit in the same transaction.
- A transactional outbox records events that must be published after commit.
- Raw source bodies are compressed and stored in PostgreSQL initially, with content hash, media type, retrieval metadata, and extractor version.
- A `BlobStore` interface isolates raw-artifact access so large payloads can later move to S3-compatible storage without changing domain contracts.
- pgvector is optional and, if introduced, stores a rebuildable derived index with embedding model/version metadata.
- Temporal owns its own persistence database. Application code accesses Temporal through its API and does not query Temporal internal tables.
- v1 is explicitly single-tenant and carries no inert `tenant_id` columns.

## Consequences

### Positive

- Business state, audit entries, and idempotency records share transactional guarantees.
- Integration tests exercise the same database class used by the deployed system.
- The system avoids SQLite concurrency differences and dual-write drift between an operational database and vector store.
- Source snapshots remain reproducible even if an embedding index is deleted or rebuilt.
- Alembic provides reviewable schema evolution evidence.

### Costs and limits

- Local development requires PostgreSQL rather than a zero-service embedded database.
- Raw bodies can increase database backup size.
- Moving blobs later requires a backfill and dual-read migration plan.
- PostgreSQL availability on a single demo host is bounded by backups and documented recovery, not high availability.

## Alternatives considered

### SQLite first, PostgreSQL later

Rejected because concurrency, locking, migration, JSON, and deployment differences would create a second persistence transition during the short delivery window.

### ChromaDB or another vector database as memory/system of record

Rejected because vector retrieval cannot represent authoritative state transitions, approvals, provenance, or transactional invariants.

### PostgreSQL plus object storage from day one

Deferred because expected snapshot volume does not yet justify another stateful service. The `BlobStore` boundary preserves the option.

### Event sourcing as the primary persistence model

Rejected because append-only audit and version history are needed, but rebuilding every read model from events is not.

## Revisit when

- snapshot volume or backup growth makes PostgreSQL blob storage operationally awkward;
- deterministic and model-assisted duplicate detection measurably benefit from semantic retrieval;
- a second real tenant requires an explicit isolation redesign;
- production availability requirements exceed the single-host demo boundary.

