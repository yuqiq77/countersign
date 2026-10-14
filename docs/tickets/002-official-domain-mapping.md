# Ticket 002 — Recognize explicitly configured official company hosts

- **Status:** closed 2026-09-28. Code `a6ae8db`; the final retry and
  file-read fixes were confirmed in the deployed worker.
- **Basis:** [ADR-005](../adr/005-official-source-authority.md), v4 §§11–12,
  17, 24. Approval rules ([ADR-013](../adr/013-human-attestation-for-unverified-postings.md))
  are unchanged.

## Summary

Authority classification used to recognize only known ATS and discovery
hosts, so a company's own careers page stayed `COMPANY_CONTROLLED` and
could never reach OPEN. Now the operator can list exact hostnames as
official in a private file. A posting on a listed host is
`OFFICIAL_COMPANY`, and it reaches OPEN only when the existing reader and
verification rules find a cited apply entry. A job board stays a board, and
vendor ATS hosts stay in the code's own list.

## What was built

- **Mapping.** A private JSON file named by
  `COUNTERSIGN_OFFICIAL_HOSTS_PATH`, loaded like the operator profile: no
  default path, and unset means no mapping. A missing, unreadable, empty,
  malformed or duplicate-host file stops the worker at start. Entries are
  bare hostnames with a company key that only labels the audit trail; an
  8-hex fingerprint of the file identifies the revision.
- **Classification.** An exact match on each observation's normalized final
  host. No wildcards, registrable-domain guesses, path rules,
  company-name matching or model-authored entries; subdomains are listed
  one by one. Redirects and follows classify by where they land. A mapping
  grants no extra fetch and widens no allow-list.
- **Trace, no schema change.** Each observation in a reading records the
  authority it received and the matched entry; the reading records the
  mapping version and fingerprint. Consumers take authority from the
  observation they use, so the evaluator's official-salary stamp follows
  the body observation, not a reused snapshot's older stamp. Records
  written before these fields read as "not recorded".
- **Retries.** A retried verification returns the stored result and body
  length, read back after commit, so an overlapping attempt cannot leave
  the workflow and the database disagreeing. A policy file that becomes
  unreadable fails as a non-retryable `PolicyError` before any fetch.
- **Replay.** `replay_reading` loads a mapping only with `--policy` and
  names the one it used.

Out of scope: company identity or relations, path rules, the trusted-board
tier (ADR-005), a mapping UI, historical policy lookup and backfilling
older readings.

## Verification

- **Commit `a6ae8db`**, fresh worktree and venv (Python 3.14.3): ruff,
  pyright and import-linter passed; `pytest -ra` **259 passed, no skips**
  on a disposable PostgreSQL database; web typecheck and build passed. No
  test loaded a real operator file; model providers were fakes.
- **Live, 2026-09-28:** the same company careers page was
  OPEN_THIRD_PARTY_ONLY without a mapping and OPEN / `OFFICIAL_COMPANY`
  with it, on the same cited mailto entry, with the policy identity
  recorded. A read-only check confirmed the rebuilt worker carries the
  final fixes.
- **Live, 2026-09-28, ADR-013 amendment checked alongside:** a read-only
  database check showed one APPROVE pinned to OPEN_THIRD_PARTY_ONLY recorded
  as request DECIDED, case APPROVED, `approved_without_official_open=true`,
  with both `approval.approve` and `human.verification_attested` audit
  events.
- **Review:** the findings, and two edges reproduced afterwards (an
  overlapping retry and an unreadable file), were fixed before closing.

## Remaining issues

- Python's IDNA and the HTTP client differ for some Unicode hostnames (such
  as `ß`), so a Unicode entry can miss its host or match another spelling.
  Use verified ASCII or punycode entries; alignment is deferred.
- A listed host covers every page on it. The apply-entry requirement bounds
  what that can turn into OPEN, but the reader can still misread an
  ordinary page.

The full working record is in git at `30b37b3`.
