# ADR-004: Govern External Calls Through a Central Capability Registry

- Status: Accepted
- Date: 2026-07-18
- Owners: Project maintainer
- Supersedes: None
- Related: [Architecture v4 §15](../job-search-agent-architecture-v4.md#15-capability-registry)

## Context

The system calls untrusted external sources and model providers. Each call needs typed inputs and outputs, authorization, domain restrictions, timeouts, rate and cost budgets, idempotency, and audit metadata. Implementing these controls separately inside source adapters, Activities, and model prompts would create inconsistent enforcement.

Most v1 calls are selected deterministically by workflow code. The design must not claim dynamic LLM tool use where none is needed, while preserving a governed path if a future model genuinely needs to request additional evidence.

## Decision

All external capability execution passes through a central Capability Registry and executor.

Each definition includes:

- stable name and semantic version;
- `invoked_by: WORKFLOW | MODEL | HUMAN`;
- strict input and output schemas;
- allowed workflows and required service/human scope;
- risk level and allowed network domains;
- timeout, retry classification, rate limit, and budget policy;
- idempotency strategy, source authority, audit policy, and owner.

The executor performs:

```text
validate request
→ authorize caller and workflow
→ enforce network/domain/risk/budget policy
→ resolve idempotency
→ execute with bounded timeout
→ validate response
→ redact sensitive fields
→ persist execution metadata and audit result
```

In v1, registered external capabilities are invoked by Workflow code. Human commands such as recording an outcome or authoring a policy proposal use authenticated API command handlers. No capability is model-invoked at launch, and the résumé does not claim LLM tool use.

The registry may support `MODEL` as a definition value, but a model cannot directly execute a capability. If a future dynamic evidence-gathering requirement is approved, the model may emit a typed intent that traverses the same executor and allowlists.

Network controls block private, loopback, link-local, and metadata-service destinations; validate hosts before and after redirects; enforce response size and content-type limits; and treat page instructions as quoted data. Secrets are resolved at execution time and never placed in model context.

## Consequences

### Positive

- External-call policy has one testable enforcement point.
- Source adapters and model providers share observability, budgets, and audit structure.
- Workflow code remains explicit about which capability is being invoked.
- Adding future model-requested evidence does not grant direct network or credential access.
- Capability versions make historical executions reproducible.

### Costs and limits

- Simple HTTP adapters incur registry/executor ceremony.
- The registry can become a service locator if internal pure functions are incorrectly registered.
- Temporal retry policy and capability error classification must have a single owner to avoid nested retries.
- A reserved `MODEL` invocation source is not evidence that model-invoked tools exist.

## Alternatives considered

### Direct HTTP/model calls from each Activity

Rejected because allowlists, budgets, validation, idempotency, and audit would drift across call sites.

### Give the model provider-native tools directly

Rejected because it bypasses server-side authorization and makes network/credential policy prompt-dependent.

### Adopt a general agent framework tool registry

Rejected because v1 does not need an autonomous planning loop, shared agent memory, or framework-owned control flow.

### Register all pure internal functions as tools

Rejected because deterministic transforms belong in domain/application code and do not cross an external trust boundary.

## Revisit when

- a measured use case requires a model to request additional evidence dynamically;
- capability count or ownership boundaries justify extracting the executor from the modular monolith;
- a standard protocol such as MCP is needed by a real external consumer;
- nested retry behavior or registry overhead becomes an observed reliability problem.

