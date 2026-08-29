# ADR-003: Separate Source Facts, Model Assessments, and Human Decisions

- Status: Accepted
- Date: 2026-07-18
- Owners: Project maintainer
- Supersedes: None
- Related: [Architecture v4 §8.1](../job-search-agent-architecture-v4.md#81-three-kinds-of-truth)

## Context

External job data changes and may conflict across sources. Model judgments are probabilistic and vary with prompt, policy, model, and context versions. Human approvals and overrides are accountable decisions. Treating all three as mutable fields on one job row would erase provenance and make it impossible to reconstruct why a recommendation was made.

This separation is load-bearing for one user: stale listings, model regressions, and changed human judgment all occur without organizational scale.

## Decision

The domain stores three truth classes independently.

### Source facts

- `SourceSnapshot` is an immutable observation of an external source at a point in time.
- Extracted facts cite snapshot spans and record extractor/version metadata.
- New observations supersede the current projection but do not rewrite earlier snapshots.
- Conflicts remain visible and are resolved by the authority policy in ADR-005.

### Model assessments

- Every `EvaluationRun` is immutable and records its Evidence Pack hash, prompt version, model route/resolution, policy version, typed output, and validation metadata.
- A later run may become current but cannot delete or mutate the earlier assessment.
- Model output is a proposal until schemas and deterministic gates accept it.

### Human decisions

- Every `HumanDecision` records actor, reviewed artifact versions, decision, structured reason, timestamp, workflow ID, and trace ID.
- An override appends a new attributed decision or fact override; it does not edit source material or a historical model response.
- Reviewer agreement and override reasons are primary feedback signals for later calibration reports.

The current `CanonicalJob` and case view are projections derived from these histories. No LLM output can overwrite a source fact or human decision.

## Consequences

### Positive

- Every important UI claim can link to evidence and a versioned decision path.
- Prompt/model/policy changes can be replayed against the same historical inputs.
- Source changes, model disagreement, and human override remain distinguishable.
- Audit and debugging do not depend on reconstructing state from application logs.

### Costs and limits

- The data model contains more records than a flat job tracker.
- Current projections require explicit supersession and selection rules.
- Retention and redaction policies must account for immutable historical records.
- UI design must present history without overwhelming the reviewer.

## Alternatives considered

### One mutable `jobs` table

Rejected because it loses the difference between observed facts, inferred judgment, and accountable decision.

### Store only raw prompts and logs

Rejected because logs are neither a typed business record nor a reliable query surface.

### Let human edits replace incorrect extracted facts

Rejected because replacement would hide the original observation and prevent extractor evaluation.

### Full event sourcing

Rejected because immutable histories and audit events are required, but all domain reads do not need to be reconstructed from an event stream.

## Revisit when

- retention law or privacy policy requires redaction/tombstoning semantics that conflict with immutable history;
- projection cost becomes measurable at real usage volume;
- additional truth classes are demonstrated by the domain rather than anticipated.
