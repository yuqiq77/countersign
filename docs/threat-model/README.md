# Threat Model

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

## Threats and controls

| Threat | Control | Status |
|---|---|---|
| Prompt injection in a job page | Treat page instructions as quoted data; no model-direct network access | Implemented (ADR-012) |
| SSRF through submitted URLs | Scheme/host validation, DNS/IP checks, redirect revalidation, response limits | Implemented (`tests/security`) |
| Duplicate effects after retry | Stable workflow/business keys and idempotency records | Implemented |
| Stale or forged job status | Authority tiers, immutable snapshots, scheduled official-source rechecks | Implemented; rechecks in live acceptance since 2026-10-14 (ADR-016) |
| Unsupported model claim | Typed cited claim objects and deterministic evidence gates | Implemented (ADR-012) |
| Unauthorized approval/override | Server-side authorization and append-only audit event | Partial: audit implemented, authorization planned |
| Credential/PII exposure | Secret references, redaction, private fixtures, no raw payloads in telemetry | Partial: secrets come from the environment, private policy stays out of the repository and is logged only by fingerprint; telemetry not built |

The detailed threat model will evolve with implemented interfaces. Multi-user adversarial security and compliance certification are explicit non-goals for v1.

