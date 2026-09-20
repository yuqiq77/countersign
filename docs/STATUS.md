# Countersign — Status

Current focus, blockers, next steps, and what waits on the owner. Rewritten
in place when any of those change; not a changelog (history: `git log`,
`docs/adr/`; rules: `AGENTS.md`). Run-by-run evidence lives in commit
messages; the private specifics of live runs live in the gitignored
`HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-09-20 |
| Branch | `main`, no remote yet. Profile loading, two-threshold salary policy, and extraction / empty-body fixes committed in `61fa0c6`; deployment follow-up below. |
| Slice | Slice 1 (one durable case): code complete; exit milestone (Track A) observed 2026-09-18; worker-kill recovery repeated 2026-09-19. |

## Now

- **Live evidence so far:** worker-kill recovery and provider-error retry
  completed without duplicate artifacts; provider request ids are logged
  and persisted. Direct-API runs reported prefix-cache reads; observed
  relay runs reported writes but no reads. Profile-driven reruns corrected
  fabricated candidate-fit claims, and configured salary bands produced a
  deterministic compensation signal. Private run details stay in `HANDOFF.md`.
- **Committed implementation.** (a) *Profile loading*:
  `COUNTERSIGN_OPERATOR_PROFILE_PATH` passed through verbatim, the
  directory mounted read-only at the same relative location in the worker,
  no implicit default path; the prompt carries either the delimited
  profile or the literal `Candidate profile: NOT PROVIDED.` — decided by
  code; `prompt_version` → `evaluator/0.2.0`; an 8-hex profile fingerprint
  in `policy_version`; the raw profile is not separately persisted or logged.
  (b) *Salary policy*: two thresholds, three private variables, blank =
  unset, all-or-nothing with a fail-fast at worker start, `ValueError`
  naming the variable; `default-rubric/0.2.0`; bands fingerprint in
  `policy_version`; salary `source_authority` / `official` stamped by code
  from the verification. (c) *Verification fixes*: SmartRecruiters'
  "I'm interested" in the apply markers with both apostrophes — a stopgap,
  future work targets structural apply evidence, not more keywords;
  JSON-LD `JobPosting` description used when visible text is empty, never
  merged (extractor `1.2.0`); a
  needs-evidence stop before the model (`mark_needs_evidence`, new edge
  `OFFICIAL_SOURCE_VERIFIED → NEEDS_MORE_EVIDENCE`, reason audited).
  Old Temporal activity results remain readable: absent text length keeps
  the original evaluation path on replay.
- **Commit verification, 2026-09-20:** each code / docs commit checked in
  a fresh detached worktree and venv: `ruff check .` and
  `ruff format --check .` clean (89 files),
  `pyright` 0 errors, `lint-imports` 3/3 kept, `pytest` 106 passed with
  no skips per commit. PostgreSQL integration used the disposable
  `countersign_review` / `countersign_review_docs` databases at
  `127.0.0.1:51479`; other fixtures used SQLite.
  `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build` passed.
  Running stack: profile loading and the two-threshold policy are deployed
  by the owner; the extraction / empty-body fixes still need deployment
  and live reruns. No live model calls were made during this review.
- **Known gaps:** JSON-LD fallback supplies posting text for evaluation;
  it does not itself prove a job is open. Ashby pages without recognized
  apply evidence still verify `UNKNOWN_STALE_RISK`; adapter work remains.
  The web detail page shows a case's state but not its recorded reason (`QUARANTINED`,
  `NEEDS_MORE_EVIDENCE`; reasons are in the audit log). Recreate the
  Compose stack whole (`up --build -d`), never a service list — a partial
  update left the Temporal UI attached to the old Temporal container.

## Next

1. Owner recreates the stack and re-runs the two Ashby and the
   SmartRecruiters cases: the Ashby pages should evaluate on their JSON-LD
   description, the SmartRecruiters posting should verify `OPEN`.
2. Make the repository public: run gitleaks once, inspect the tree for
   private identifiers (see `AGENTS.md` constraints), and have the owner
   add the remote and push.
3. **Slice 2 — governed intelligence** (timebox 3 weeks, kill rules in v4
   §24): watchlist as a policy artifact including company → official-domain
   mapping (this is what unlocks `OFFICIAL_COMPANY`); Greenhouse + Lever
   adapters; scan-report import; scheduled scans + stale rechecks;
   capability-registry policy enforcement; gateway route registry; anchor
   calibration cards; OTel traces + cost dashboard. Structural apply
   evidence per ATS belongs to the adapters. Exit = Claim Level 1 (§25) +
   scheduled scans running. Tickets under `docs/tickets/` start here: one
   per workstream, cut and owner-read before code.
4. **Slice 2.5 — company-research loop** per ADR-011, after Slice 2
   (registry enforcement and the gateway route registry are prerequisites).
   Kill rule: the `CompanyProfile` schema ships regardless; an
   operator-filled profile is the degraded mode.
5. v4 edits, owner-reserved and one line each: §9 diagram gains
   `OFFICIAL_SOURCE_VERIFIED --> NEEDS_MORE_EVIDENCE`; §15 / §24 / §29
   mention the research loop when Slice 2.5 nears.

## Waiting on the owner

- **Rubric follow-ups, golden set first.** Whether the no-profile mode
  gets nullable candidate-specific scores (`int | None`,
  `seniority_fit=UNASSESSABLE`) in Slice 2; whether a HARD blocker should
  bind the decision band (one HARD location blocker led to `SKIP`, another
  to `STRETCH`) — a prompt rule keeps the human in the loop, a domain gate
  would also amplify a mislabelled blocker; tightening the compensation
  rule to "no risk flags about pay level"; whether technical
  responsibility and coordination work are separated correctly once a
  profile is present (`drift_risk` penalizes coordination by definition).
  The over-enthusiasm regression case is the 2026-09-19 posting re-run on
  2026-09-20.
- **Usage accounting.** An unexplained input-token outlier and the lack
  of cache reads in observed relay runs remain follow-ups; evidence is in
  `HANDOFF.md`. The Slice 2 cost dashboard must price uncached input,
  cache writes, cache reads, and output separately using the route's rates.

## Recently done (pointers only)

Slice 1 commit series + ADR-011 (`git log b87cb11..44f54da`); delivery and
working-agreement updates (`git log 44f54da..a79236c`); Track A follow-up
(`a80ac12`); formatting cleanup and CI gate (`89730d4`); salary-policy
foundation (`0ec1882`); profile loading, two-threshold policy, and
verification fixes (`61fa0c6`).
