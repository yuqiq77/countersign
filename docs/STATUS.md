# Countersign — Status

Current state and next actions. What is built is in the
[README](../README.md#current-scope), decisions in [ADRs](adr/README.md),
verification evidence in each [ticket](tickets/README.md). Private live-run
details stay in the gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-11-03 |
| Branch | `main`, tracking `origin/main`; deployed from `b4b747e` |
| CI | [GitHub, Python 3.12.15](https://github.com/yuqiq77/countersign/actions/runs/40965745358): 394 tests passed, 3 browser tests skipped (Chromium was not configured); the 3 browser tests passed separately in the Python 3.12.16 runtime image; static checks and web typecheck/build passed |
| Slice | Slice 1 and its core live paths verified; release boundary `v0.1.0` |
| Focus | Ticket 005's live trial: runs completed within budget, but most search admissions were closed or unreadable |
| Deployed | Ticket 005 on 2026-10-29: API, worker and web rebuilt, schema `0002` applied in place, existing records kept |
| In force | `page-reader/0.5.0`, `evaluator/0.3.2`; `discovery-{brief,search,screen}/0.1.0` |

## Next

1. Greenhouse Job Board API ([ticket 006](tickets/006-greenhouse-adapter.md)):
   implemented, not deployed. Once deployed, a hosted Greenhouse posting
   verifies OPEN from the API without a model call, one embedded on a
   company page is evaluated on the API's description, and the watch skips
   prospect posts.
2. Profile-driven discovery ([ADR-017](adr/017-profile-driven-discovery.md),
   [ticket 005](tickets/005-profile-driven-discovery.md)), live since
   2026-10-29: three runs completed without failures, within every budget.
   Two of the eight search admissions were verified OPEN outside the
   watched boards; five were closed at verification and one was unreadable.
   Four returned HTTP 404, so a check before admission could exclude those
   four (a follow-up ticket is drafted separately). Two passed candidates
   waited until the next run; one was then verified OPEN and one CLOSED.
   The queries barely varied between the two calendar runs, a weekend
   apart. The two-week side-by-side needs a sample of the external
   agent's report.
3. Ticket 004's two review findings: a recheck not serialized with a
   concurrent decision, and Ashby secondary locations not extracted.
4. Then registries, calibration, traces and cost (v4 §24); research stays
   Slice 2.5 (ADR-011).

## Not yet observed live

JSON_LD_DIRECT_APPLY as the selected entry, the citation-repair step and
CLOSED reached through a followed page; for the watch, closure by board
absence, a decision concurrent with a closure, missed-run catch-up and
overlap skipping; for discovery, a lead and a URL rejected by the result
check. Tests cover these rules; the Schedule lifecycle was checked on the
real server.

## Known limits

- **Discovery** ([ticket 005](tickets/005-profile-driven-discovery.md)):
  search results can be stale, so dead postings reach admission and close
  at verification; an aggregator's repost on an ATS host counts as an
  official ATS posting.
- **Watch** ([ticket 004](tickets/004-scheduled-watch.md)): the two review
  findings under Next. Greenhouse postings close only through the decision
  table or approval expiry; two observed company-page embeds supplied no
  job body (UNVERIFIED / WAIT), which ticket 006 addresses once deployed.
  Candidates left over by the budget can age out; the Schedule lifecycle
  has no automated test.
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

## Decisions pending

Rubric and blocker wording, revisions of the discovery prompts after the
trial, a liveness check for search candidates, no-profile scoring,
official-domain and board policy, public claims, and v4 updates for the
reader, the evidence loop and ADR-017 (§11 discovery, §15 the first
model-chosen tool). Deferred: structured context effects, stricter cookie
matching, evaluator replay and generic JS navigation.
