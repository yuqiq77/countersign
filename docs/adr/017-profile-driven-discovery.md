# ADR-017: Discovery inside the system — a profile-driven search and a coarse screen

- Status: accepted (2026-10-22)
- Date: 2026-10-19
- Supersedes in part: [ADR-016](016-scheduled-watch.md), decision 2's "the
  watch never judges relevance" and its rejected alternative "LLM triage of
  candidates before intake"; ADR-016's status line records this.
- Extends: [ADR-011](011-bounded-company-research-loop.md) (invariants for
  model-chosen retrieval), [ADR-012](012-bounded-page-reader.md) (the reader
  stays the only model-driven page navigation), ADR-016 (Schedule, budgets,
  case admission).
- Amends [v4](../job-search-agent-architecture-v4.md) §11 (the discovery
  premise and item 5) and §15 (the first model-chosen tool); the v4 text
  follows separately.

## Context

v4 §11 assumes that the primary discovery for this market is based on the
candidates themselves and leaves the system everything after discovery. In
practice discovery is a scheduled, general-purpose agent outside the
system, the one the README's "Why this exists" describes, working from its
own statement of the operator's goals. The operator therefore keeps two
sources of truth:

- two statements of intent: that agent's goals and the operator profile the
  evaluator reads;
- two inventories: what that agent has reported, and the case list;
- two relevance judgments, which can disagree.

v4 §11 item 5 would import that agent's reports. That connects the
inventories, but it keeps two statements of intent and makes the seam
permanent. v4 itself calls the agent legacy and wants a report to be a view
over cases (§11, last paragraph): the intended end state is one source of
truth, but the system has no way to discover on its own.

The watch (ADR-016) does not close the gap: it is limited to the boards on
a known watchlist, so a company the operator has not found yet never
enters.

The load-bearing facts: the duplicated intent is observed, not
hypothetical; and finding postings is open-ended, since which sites and
companies to look at depends on what a search returns. That is what
model-chosen retrieval is for (ADR-011).

## Decision

1. **The scheduled run gains a search step.** The run ADR-016 defines (one
   Schedule, one run per tick, ADR-001) adds a search beside board listing.
   One Activity asks a model, through the gateway, with the provider's
   server-side web search tool and no other tool, for currently open
   postings that match the search brief (item 2) in the policy's regions.
   The output is a typed list of candidates (URL, title, company, location
   text and the model's reason), every field required. It carries no
   publication date: a search result's page age dates the page, not the
   posting, and is not used as one. Code accepts a candidate only if its
   URL appeared in the run's own search results (scheme, host and path,
   normalized), so an invented URL never enters, and the queries the model
   issued are recorded. The search model has no fetch tool: pages are read
   only by the bounded reader after intake (ADR-012).

2. **The search sees a brief, not the profile.** The search's queries leave
   the model provider, so it gets only what searching needs: a brief
   derived from the operator profile (the file the evaluator reads,
   `COUNTERSIGN_OPERATOR_PROFILE_PATH`). The small-model route derives it
   once per profile fingerprint as typed lists of short entries (target
   role families, skills, regions, languages and roles to exclude) with no
   free-text field. The brief is stored with that fingerprint and shown to
   the operator. The search model's context holds the brief and its
   instructions only, so its queries cannot carry the rest of the profile.
   The screen and the evaluator keep the full profile, which reaches only
   the model provider, as evaluation does today.

3. **One coarse screen for every source.** Search candidates, and
   watched-board postings that pass the structured filter, go through one
   screen Activity before intake. A small-model route judges each candidate
   against the profile from its listing facts: title, company, location
   texts, department and team where the API gives them, and a capped
   excerpt of the description where one is available. It answers PASS or
   DROP with a one-sentence reason. It judges direction only, whether the
   role is the kind of work the profile seeks, and leaves seniority,
   feasibility and fit to the evaluator. Doubt passes. A screen that fails
   (a refusal, a contract failure after repair, a timeout) passes the
   candidate and records why. The screen is a workflow-invoked model call,
   as the evaluator is (v4 §15 `evaluate_job_fit`); v4 §13.3 already
   reserves a small model for classification.

4. **Every candidate is recorded; a drop is never silent.** A candidate
   record keyed by posting identity (the ATS key where the URL parses, else
   the normalized URL) holds what was seen and where (a board, or a search
   and its query), when it was first and last seen, the screen's verdict
   and reason with its route and prompt version, and the case it became,
   if any. A verdict holds only for the inputs it was made from: the
   profile fingerprint, the screen's prompt version and route, and the
   listing facts it read (the title, and a board posting's publication
   timestamp). When any of them changes the verdict is stale, and a stale
   verdict, PASS or DROP, is screened again before it counts. A board
   posting is screened again when it is next listed, which every run does
   while it stays in the window. A search may never return a result again,
   so the next run screens again the stale search drops last seen within
   the lookback period (`published_within_days`); an older one is screened
   again only if a search returns it. Dropped candidates and leads (item 5)
   are listed for the operator, who can paste any of them. The case list
   plus these records is the one inventory. This is a schema change, so it
   comes with a migration after the baseline.

5. **Terms of service bound automated intake.** Automated intake never
   fetches a host whose terms forbid automated access; LinkedIn is the
   standing case (v4 §5). A search result on such a host is recorded as a
   lead, not admitted, and the search step is told to return the company's
   or the ATS's own posting URL instead. LinkedIn offers no public API for
   reading or searching jobs (its job APIs are partner programs for
   publishing them), and MCP servers for it drive a logged-in session or
   unofficial endpoints, which its user agreement prohibits; neither is an
   input here. Pasting a URL by hand is unchanged.

6. **Intake as for the watch, in an order each source can date.** PASS
   candidates that are not known become cases, with ids derived from the
   posting, through the unchanged pipeline: verification decides status and
   authority (a search result is discovery data; only known ATS hosts and
   mapped company hosts are official, ADR-005), the evaluator judges fit,
   and the human decides. Board postings keep ADR-016's order within their
   source: newest first by the vendor's publication timestamp, within the
   lookback window. A search candidate has no publication date before
   verification, so search candidates queue in the order they first passed
   the screen, oldest first (ties in the order the run recorded them); a
   drop that passes on a later screening joins at the back. The two queues
   take turns (item 7), and each run records their lengths. A search
   candidate's own date, where a source states one (an ATS timestamp, the
   page's JSON-LD `datePosted`), appears only at verification and plays no
   part in admission.

7. **Four budgets, fairness by turns, numbers from live runs.** The policy
   caps, per run:
   - searches (`max_searches_per_run`), which bounds what leaves through
     the provider's search;
   - discovery model calls (`max_discovery_model_calls_per_run`), one
     budget for the brief, the search and the screening of both sources;
   - new cases (ADR-016's `max_new_cases_per_run`, now counting both
     sources), which bounds full evaluations and the reviewer's queue;
   - recheck model calls (`max_recheck_model_calls_per_run`, unchanged).

   The discovery budget goes first to the brief when the profile has
   changed, then to the search, whose searches the first cap bounds, then
   to screening. Screening, re-screening included, and admission each take
   one candidate from each source in turn, every source in its own order;
   the source with the first turn alternates from run to run, so an odd
   number of slots favors neither, and when one source has nothing left
   the other goes on with what remains. Fairness comes from the turns, not
   from a split of the budgets. A posting both sources return is one
   candidate and stays with the source that recorded it first. Calls are
   charged as ADR-016 charges rechecks, the worst case before each call
   and the unused part credited back, so no budget is exceeded. Usage is
   recorded on every search, brief, screen and evaluation and on the run's
   audit event. This ADR fixes the mechanism, not the numbers: the
   operator sets first values for the live trial and revises them from
   recorded usage. Candidates beyond a budget wait for the next run.

   Routes are configuration: the brief, the screen and the search run on a
   Haiku-class route, the search with the provider's dynamic result
   filtering on; a Sonnet-class route is the fallback for the search if
   result quality calls for it.

8. **The external agent is retired after a side-by-side.** Both run for two
   weeks. The external agent's reports for that period are compared once,
   privately, with the candidates recorded here: what it found that this
   search missed, and what the screen dropped that it rated relevant. When
   the operator accepts the difference, the external agent is retired and
   the profile is the only statement of intent. The import adapter of v4
   §11 item 5 is not built; its role shrinks to this one comparison.

## Consequences

- One profile drives the brief, the screen and the evaluation, and one
  inventory holds every posting seen. Changing the profile re-derives the
  brief and makes every verdict stale: recent drops are screened again,
  and older ones when a board or a search shows them again.
- The system now discovers on its own and judges relevance before intake.
  Both stay proposals: nothing a search finds is official until verified,
  nothing the screen passes is approved until the human signs, and every
  drop is recorded and listed.
- The model chooses a tool for the first time in production: the
  provider-side web search, whose dynamic filtering runs model-written code
  over the results in the provider's sandbox and reaches nothing in this
  system. It runs outside the system's capability executor, so the
  controls sit at the request (a use cap, no other tool, the brief as the
  only context, the run's budget) and at the output (the contract, the
  URL-in-results check, the recorded queries).
- Search candidates are admitted in the order they passed the screen, not
  the order they were published. A posting found late is verified late,
  and one that closed meanwhile ends at verification, before any
  evaluation.
- Only the brief can leave through the provider's search; the full profile
  reaches the model provider alone, as for evaluation. The brief and the
  queries are recorded, so what left is auditable.
- A relevant posting the screen wrongly drops is the new failure mode. It
  stays visible in the dropped list, and the side-by-side measures how
  often it happens.
- The same posting found on several hosts can become several cases until
  canonical merge exists (v4 §29); the search is told to prefer official
  URLs, and a duplicate costs one evaluation.
- Search results and listing text are untrusted data. The search output is
  confined to the contract and to URLs from its own results; a hostile page
  can cost a screen call and an evaluation, nothing more.
- Cost moves from full evaluations of off-direction postings to searches
  and screen calls. Per-call usage is recorded; the numbers come from the
  live trial.

## Alternatives considered

- **Import the external agent's reports permanently** (v4 §11 item 5).
  Rejected: it keeps two statements of intent and makes the seam permanent.
- **Structured filters only** (titles, departments). Rejected: titles miss
  what the posting says, and departments are each company's internal
  labels, unknown in advance.
- **Separate screening and admission budgets per source.** Rejected: six
  settings instead of four, and a fixed split leaves one source's share
  unused while the other has candidates. Turns protect each source and use
  the whole budget. A shared budget without turns was rejected too:
  whichever source is screened first could use it up.
- **The evaluator as the only screen** (ADR-016 as accepted). Rejected:
  every candidate becomes a full evaluation and a pending approval, and at
  search volume the queue fills with postings a glance would drop. ADR-016
  also counted triage as a widening of the `MODEL` class; under v4 §15 a
  workflow-invoked model call is not in that class, so its remaining
  reason, that the evaluator already makes the judgment, is what this ADR
  revisits.
- **Let the search model judge relevance and skip the screen.** Rejected:
  board postings pass no search model, and one screen gives every source
  the same recorded judgment. Keeping retrieval and judgment apart keeps
  each prompt small and each record clear.
- **The full profile for the search model.** Rejected: its queries leave
  the model provider, and the brief bounds what can leave.
- **An operator-written search brief.** Rejected: a second statement of
  intent to keep in step with the profile, the problem this ADR removes. A
  derived brief the operator can see gives the same control over what
  leaves.
- **A fetch tool for the search model.** Rejected: it would be a second
  model-driven page navigation beside the reader (ADR-012). Result URLs are
  enough.
- **LinkedIn through an API, an MCP server or a data vendor.** Rejected:
  there is no public read API, MCP servers automate a logged-in session
  against its terms and put the operator's account at risk, and vendor
  feeds are scraped data (v4 §5, §11).

## Revisit when

- The side-by-side shows relevant postings the external agent finds and
  this search misses.
- The screen drops postings the operator later wants, more than
  occasionally.
- The derived brief needs correcting more than occasionally; then a brief
  the operator writes may be worth its upkeep.
- Search cost per relevant case exceeds what the operator accepts.
- Most search candidates turn out to sit on ATS boards that state a
  publication timestamp; those could then join the board order.
- A source offers a terms-clean read API (LinkedIn included).
- Cross-host duplicates become frequent enough to justify canonical merge
  (v4 §29).
