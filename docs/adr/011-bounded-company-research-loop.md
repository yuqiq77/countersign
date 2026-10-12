# ADR-011: Bounded Company-Research Loop Activates Model-Invoked Capabilities

- Status: Accepted
- Date: 2026-08-29
- Owners: Project maintainer
- Supersedes: None (activates the path reserved by ADR-004)
- Related: [ADR-004](004-central-capability-registry.md), [Architecture v4 §15](../job-search-agent-architecture-v4.md#15-capability-registry)

## Context

Evaluating a case often requires company intelligence the posting does not
contain: funding stage and recency, team composition, product substance,
domain lock-in signals. The operator has performed this research manually,
case by case — the measured, recurring cost that ADR-004's revisit clause
("a measured use case requires a model to request additional evidence
dynamically") was written for.

Company research is genuinely open-ended: which source to consult next
depends on what the previous step found. A deterministic pipeline cannot
enumerate the plan in advance. This is the load-bearing fact that justifies
dynamic, model-directed retrieval — and nothing less would.

## Decision

Implement company research as a **bounded agent loop inside one Temporal
Activity**, with durable state in PostgreSQL, and register the loop's
retrieval capabilities as the first members of the `MODEL` invocation class.

### Ownership split

- **Temporal** owns the research run's lifecycle: start, retry, timeout,
  cancellation, heartbeats.
- **PostgreSQL** owns the immutable execution ledger: every step's tool
  execution, model invocation, and evidence claim, keyed by
  `(research_run_id, step_id)`.
- **The loop** holds only in-memory typed state (`ResearchState`),
  rebuildable from the ledger.
- **The gateway** remains the only entry for model calls; **the capability
  registry executor** remains the only entry for tool execution.

### Recovery semantics

- **Floor (must always hold):** every write is idempotent under
  `(research_run_id, step_id)`; read-only retrievals may re-execute;
  duplicated model spend on an Activity retry is accepted.
- **Target:** on retry, rebuild `ResearchState` from the ledger — a pure
  function of the records, covered by a golden replay test — and resume
  from the next step. Resume is an optimization layered on the floor's
  invariants, never a replacement for them.

### Budgets and honesty

- Iteration cap, token/cost budget, and wall-clock budget are enforced by
  our code, never delegated to the model. Wall-clock is measured per run
  (from `started_at`), not per attempt.
- The run records `stop_reason: DONE | ITERATION_CAP | BUDGET_CAP |
  WALL_CLOCK_CAP | CANCELLED | ERROR`. A profile produced by an exhausted
  budget declares itself incomplete.

### Security invariants

- **No MODEL-invoked capability may have side effects.** Read-only
  retrieval only, asserted by a registry policy test. A write capability in
  the MODEL class requires a new ADR; this one can never authorize it.
- Discovery-style retrieval gets a **public-web read-only** fetch policy
  class: SSRF address pinning, size/content-type limits, and recorded
  robots/terms policy — an explicit, documented relaxation of fixed domain
  allowlists, confined to this class.
- Fetched text is quoted data; prompt-injection fixtures (hostile company
  pages) join the security golden set.
- Loop output is a proposal: results enter the case and pass through the
  human approval flow like every other assessment.

### Output shape

`CompanyProfile` is a **company-level aggregate** shared across cases:
typed per-field claims with evidence citations and `SourceAuthority`
labels, `observed_at`, and a staleness policy. Research runs are
append-only (a new run supersedes; old runs remain), and approvals pin the
profile version reviewed. In v1 the loop is **operator-triggered** (a HUMAN
command starts the run); only the loop's internal retrieval calls are
model-invoked.

### Sequencing

Implementation follows Slice 2 (registry policy enforcement and gateway
route registry are prerequisites) as a timeboxed slice of its own. Kill
rule: the `CompanyProfile` schema ships regardless; the loop is its
automated filler, and an operator-filled profile is the degraded mode. The
§25 claim upgrade ("tool use by an LLM") applies only after this ships.

## Consequences

### Positive

- The reserved `MODEL` class is activated by a measured need, with every
  guardrail (registry, gateway, budgets, human approval) already standing.
- Research output is ordinary evidence — claims and snapshots reusing the
  existing provenance machinery, not a parallel data model.
- Crash recovery extends the worker-kill demo to the agentic path: resume
  at step k with budget and findings intact.

### Costs and limits

- A search-provider dependency enters the cost envelope.
- Public-web retrieval enlarges the injection surface; mitigations above.
- Until ledger resume ships, an Activity retry re-spends model tokens.

## Alternatives considered

### Nested LangGraph inside the Activity

Rejected for now. Precisely: LangGraph checkpointing is not architecturally
contradictory — Temporal owns the business lifecycle while a checkpoint
would own one run's reasoning state — but it is currently unjustified: a
5–10 iteration linear plan-act-integrate loop does not pay for the added
recovery, cancellation, version-compatibility, and observability surface,
and the framework's model bindings would bypass the gateway's budget and
version capture unless wrapped. There is no reason to enable it, which is
different from a reason to forbid it.

### Workflow-driven loop (one Activity per iteration)

Equivalent durability is available more cheaply: the execution ledger gives
per-step recovery without growing workflow history or paying a workflow
round-trip per iteration. Rejected in favor of one Activity plus ledger.

### Unstructured loop without a ledger

Rejected: a crash re-spends the whole run, results are not reproducible,
and evidence would bypass the audit trail.

### Fully deterministic research pipeline

Rejected: the retrieval plan is not enumerable in advance — this is the
load-bearing fact of the whole decision.

## Revisit when

- The loop's topology outgrows a linear cycle (parallel branches,
  delegation between specialized steps) — re-evaluate a graph framework
  against the same gateway/registry constraints.
- Research volume makes per-run budgets insufficient as the only cost
  control.
- Anyone proposes a MODEL-invoked capability with side effects — that
  conversation starts from a new ADR, not from this one.
