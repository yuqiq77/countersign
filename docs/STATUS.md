# Countersign — Status

Current state and next actions. What is built is in the
[README](../README.md#current-scope), decisions in [ADRs](adr/README.md),
verification evidence in each [ticket](tickets/README.md). Private live-run
details stay in the gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-10-15 |
| Branch | `main`, tracking `origin/main` |
| CI | GitHub Python 3.12.15: 345 tests passed, 3 browser tests skipped (Chromium not configured); static checks and web typecheck/build passed |
| Slice | Slice 1 and its core live paths verified; release boundary `v0.1.0` |
| Focus | Ticket 004 Schedule and initial live backfill/recheck verified; two-week acceptance started; two review findings remain |
| Deployed | Ticket 004 watch and ticket 003 final ATS-host line confirmed in the worker; subsequent stored-payload refactor not rebuilt |
| In force | `page-reader/0.5.0`, `evaluator/0.3.2` |

## Next

1. Complete ticket 004's two-week live acceptance, started 2026-10-14
   (earliest completion 2026-10-28), and address its two review findings.
   The private watchlist is active; initial backfill, dedupe, rechecks
   and Schedule create/update/pause/resume passed on the deployment.
   Observe scheduled ticks and any takedown/republication, including
   whether Ashby moves `publishedAt`. Evidence is in the ticket.
2. Then the scan-report import, registries, calibration, traces and cost
   (v4 §24); research stays Slice 2.5 (ADR-011).

## Not yet observed live

JSON_LD_DIRECT_APPLY as the selected entry, the citation-repair step,
CLOSED reached through a followed page, and watch closure/republication.
Watch runs have been triggered through the real Schedule; calendar ticks,
missed-run catch-up and overlap skipping have not yet been observed live.
Tests cover the case rules; Schedule lifecycle was checked on the real server.

## Known limits

- **Watch** ([ticket 004](tickets/004-scheduled-watch.md)): two open review
  findings, a recheck not serialized with a concurrent decision and Ashby
  secondary locations not extracted. Greenhouse postings close only through
  the decision table or approval expiry; candidates left over by the budget
  can age out; Schedule lifecycle has live verification but no automated test.
- **Ashby** ([ticket 003](tickets/003-ashby-adapter.md)): a page with no
  text and no JSON-LD stops at NEEDS_MORE_EVIDENCE even when the API says
  OPEN.
- **Official hosts** ([ticket 002](tickets/002-official-domain-mapping.md)):
  use ASCII or punycode entries, since IDNA differs from the HTTP client; a
  mapped host covers all its paths, and the reader can still misread a page
  on it.
- **Fetch and render:** host-only cookies reach subdomains; a redirect to a
  named resource host can send one GET before it is rejected (ADR-015); a
  button without an href cannot be followed; some cross-host pages do not
  render, and blocking DNS or library calls can outlast the reader budget.
- **Reading and evaluation:** citations prove provenance, not correctness;
  ±1 score movement is treated as noise until calibration; route
  differences seen on a relay wait unless they recur.
- **UI:** approvals without an official OPEN and quarantine reasons appear
  only in audit records.

## Owner decisions pending

Rubric and blocker wording, no-profile scoring, official-domain and board
policy, public claims, and v4 updates for the reader and evidence loop.
Deferred: structured context effects, stricter cookie matching, evaluator
replay and generic JS navigation.
