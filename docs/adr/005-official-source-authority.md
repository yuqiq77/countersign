# ADR-005: Official-Source Authority and Stale-Job Policy

- Status: Accepted
- Date: 2026-07-18
- Owners: Project maintainer
- Supersedes: None
- Related: [Architecture v4 §§11–12](../job-search-agent-architecture-v4.md#12-source-provenance-and-official-verification)

## Context

Job intelligence is collected from sources with different authority and freshness. Aggregators and search results may be stale, reposted, incomplete, or linked to a closed official posting. HTTP 200 does not prove that a role accepts applications, while a temporary source failure does not prove closure.

Recommending an unavailable role is a domain failure, not merely a scraping error. The system needs deterministic authority and approval rules before model-based fit evaluation can be trusted.

## Decision

Sources use this authority order:

1. official company career page or official ATS API/page;
2. company-controlled recruiting page;
3. trusted job board with a working redirect to the official application path;
4. aggregator, report, search result, or social post — discovery only.

Conflicting observations are not averaged. The higher-authority observation drives the current projection, and the conflict remains visible.

The canonical status enum is:

- `OPEN` — an official source is reachable and semantically accepting applications;
- `OPEN_THIRD_PARTY_ONLY` — a third party appears open but no official source is available;
- `UNKNOWN_STALE_RISK` — evidence is insufficient, conflicting, or temporarily unavailable;
- `CLOSED` — an authoritative source says the job no longer accepts applications;
- `EXPIRED` — an explicit expiry or confirmed stale listing.

Only freshly verified `OPEN` cases pass the normal `APPLY_NOW` approval gate. Any override records the actor, reason, and exact evidence reviewed.

Verification rules:

- store every observation as an immutable snapshot with canonical URL, authority, retrieval time, HTTP status, content hash, raw-content reference, extractor/version, observed status, redirect chain, and cited spans;
- follow and record the official application path;
- validate page/API semantics rather than HTTP status alone;
- distinguish transient source failure from closure;
- treat disappearance or 404 after a prior verified observation as closure evidence, with a second observation when source health is uncertain;
- reverify immediately before approval and before a user records submission;
- schedule rechecks for active cases;
- trigger reevaluation when the normalized content hash changes materially;
- record per-adapter robots/terms policy and do not scrape prohibited sources.

LinkedIn and similar aggregators are manual discovery inputs only. The system does not automate scraping that violates source terms.

## Consequences

### Positive

- Discovery breadth cannot silently override official truth.
- Stale and closed roles are prevented from ordinary “apply now” recommendations.
- Every status can be reproduced from a specific observation and extractor version.
- Source conflicts and transient outages remain visible instead of being collapsed into false certainty.
- Status regression cases are deterministic fixtures rather than subjective LLM evals.

### Costs and limits

- Official-source discovery and semantic status extraction vary by ATS/provider.
- Some roles remain `OPEN_THIRD_PARTY_ONLY` or `UNKNOWN_STALE_RISK` and require human judgment.
- Scheduled reverification creates external calls that require rate limits and source-health monitoring.
- An official page can still contain inaccurate information; this policy establishes authority, not absolute truth.

## Alternatives considered

### Treat the first discovered source as authoritative

Rejected because aggregators are frequently stale and duplicate official listings.

### Treat HTTP 200 as open

Rejected because closed templates and expired job pages often remain reachable.

### Let the evaluator infer job status together with fit

Rejected because source status is a deterministic safety gate and should not be blended into a probabilistic recommendation.

### Discard lower-authority observations after finding the official page

Rejected because they remain useful for deduplication, change detection, and provenance.

## Revisit when

- a source exposes a signed or authenticated status API with stronger semantics;
- adapter failure data justifies source-specific closure thresholds;
- legal or terms changes prohibit an existing collection method;
- application submission becomes an in-scope workflow and requires a stronger final verification transaction.

