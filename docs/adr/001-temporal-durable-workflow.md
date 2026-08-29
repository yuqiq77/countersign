# ADR-001: Temporal as the Only Durable Workflow Engine

- Status: Accepted
- Date: 2026-07-18
- Owners: Project maintainer
- Supersedes: None
- Related: [Architecture v4 §§9–10](../job-search-agent-architecture-v4.md#10-durable-workflow-design)

## Context

A Job Intelligence Case crosses unreliable external HTTP calls, probabilistic model calls, scheduled source rechecks, bounded retries, and a human approval that may arrive days later. These constraints exist with one user. Process-local orchestration, cron plus database flags, or a prompt-defined agent loop would require us to rebuild durable timers, recovery, retry history, and message delivery.

The system needs to demonstrate recovery from worker failure without duplicating business effects. It does not need a second background-job substrate or a general-purpose agent graph.

## Decision

Temporal is the only outer workflow engine and background-job system.

- `JobIntelligenceWorkflow` owns durable ordering, business-state transitions, timers, cancellation, and approval waits.
- All network, database, filesystem, parser, and model I/O runs in Activities.
- Human approval reaches the waiting Workflow through an idempotent Signal.
- Temporal Schedules trigger watchlist scans, stale-job rechecks, outcome reminders, and calibration reports.
- Task queues remain logically separate by workload. One Python process may host their Workers until queue metrics show contention.
- Workflow payloads use versioned contracts. CI replays representative histories to detect non-deterministic changes.
- Long-lived monitor workflows use Continue-As-New when history size warrants it.
- Temporal is self-hosted for local development and the initial demo for cost and reproducibility. This demonstrates process/worker crash recovery, not host or zone high availability.

LangGraph is not part of the outer runtime. A narrow reasoning graph may later run inside one Activity if a measured cyclic-reasoning requirement appears; Temporal remains the durable owner.

## Consequences

### Positive

- Multi-day waits do not occupy an API process.
- Activity retry and timeout history is durable and inspectable.
- Worker restarts do not lose workflow progress.
- Schedules, Signals, and replay testing use one coherent execution model.
- Redis/Celery and a custom workflow-state polling loop are unnecessary.

### Costs and limits

- Workflow code must obey Temporal determinism constraints.
- Workflow and Activity boundaries require deliberate contract design.
- The local and demo stack must operate a Temporal service.
- At-least-once Activity execution still requires application-level idempotency.
- A single-host demo is not highly available; host recovery depends on backup and redeploy procedures.

## Alternatives considered

### Celery or another queue plus PostgreSQL state

Rejected because the project would still need to implement durable human waits, timers, state transitions, workflow history, and safe replay separately.

### LangGraph as the outer orchestrator

Rejected because the load-bearing problem is durable business execution, not open-ended cyclic planning.

### Application code plus cron and status columns

Rejected because recovery and retries would become bespoke distributed-systems code central to every feature.

### Temporal plus Celery

Rejected because two background-job systems would duplicate operational concerns without a current workload requirement.

## Revisit when

- Temporal operational cost materially exceeds the value of durable waits and recovery;
- a concrete Activity needs cyclic reasoning, in which case only the internal reasoning implementation is reconsidered;
- queue metrics demonstrate that worker processes require deployment isolation;
- a second deployment target changes the self-hosted-versus-managed service trade-off.

