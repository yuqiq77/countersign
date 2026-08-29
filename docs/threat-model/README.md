# Initial Threat Model

## Protected assets

- candidate profile, evidence catalog, outcomes, and compensation constraints;
- source snapshots and derived assessments;
- model and source credentials;
- approval, override, audit, and policy history.

## Trust boundaries

- external pages and APIs are untrusted data;
- the browser is not an authorization boundary;
- model output is untrusted until schema and deterministic policy validation;
- the worker uses a scoped service identity;
- database and Temporal endpoints remain private.

## Initial threats and controls

| Threat | Initial control |
|---|---|
| Prompt injection in a job page | Treat page instructions as quoted data; no model-direct network access |
| SSRF through submitted URLs | Scheme/host validation, DNS/IP checks, redirect revalidation, response limits |
| Duplicate effects after retry | Stable workflow/business keys and idempotency records |
| Stale or forged job status | Authority tiers, immutable snapshots, scheduled official-source rechecks |
| Unsupported model claim | Typed cited claim objects and deterministic evidence gates |
| Unauthorized approval/override | Server-side authorization and append-only audit event |
| Credential/PII exposure | Secret references, redaction, private fixtures, no raw payloads in telemetry |

The detailed threat model will evolve with implemented interfaces. Multi-user adversarial security and compliance certification are explicit non-goals for v1.

