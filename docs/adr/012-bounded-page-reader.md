# ADR-012: A bounded page reader in front of verification

- Status: accepted
- Date: 2026-09-20
- Supersedes: nothing. Activates the reserved `MODEL` invocation class
  (ADR-004, ADR-011) for a second measured need.

## Context

Verification used a static fetch and fixed apply phrases. A small survey
of real postings showed that labels, client-rendered controls and links to
application pages vary. Controls and JSON-LD often provide enough input;
interpreting their purpose still matters. A `mailto:` can mean contact or
sharing, and an apply-looking control can save a job for later.

## Decision

Run one bounded reader loop inside `verify_status`. Code extracts text,
control labels and paired targets (including accessibility labels and
submit inputs), and JSON-LD JobPosting fields. The model interprets those
facts and returns a typed `DONE`, `FOLLOW`, or `RENDER` step with a reading.
It sees no operator profile and does not judge fit, salary or the company.

- **Actions:** FOLLOW fetches only an observed http(s) href, under the
  existing SSRF, redirect and body-size policy. RENDER observes the current
  page, including a page reached by FOLLOW, once per URL. Rendering is
  requested by the model, with one code-initiated exception recorded as
  step 0: when static extraction of the first page yields no HTML text and
  no controls (a client-rendered shell), code renders it before the first
  model step. The model still decides what the rendered page means.
- **Budgets:** three steps, two follows, one schema repair per step, and a
  120-second reading budget after the initial fetch. Remaining time is
  passed to provider/fetch/render calls and checked after each returns;
  late results are discarded. Bounded provider calls disable SDK retries.
  Synchronous DNS/library calls are not forcibly cancelled; the Activity's
  three-minute timeout remains the outer limit, with time left for saving
  normal timeout results. There is no additional scheduler or retry loop.
- **Evidence:** the reading's apply entry is a required object with an
  explicit `NONE` kind, not an optional one: under constrained decoding the
  null branch was the path of least resistance, and live models on both
  routes described a working Apply control in their notes while leaving the
  field null; the same page was cited correctly once the choice had to be
  made. A cited control must match its observed label **and target**;
  JSON-LD claims must match the actual field. Other spans are checked with
  whitespace/case folding. Rejected claims remain in the reading. These
  checks establish provenance; the meaning of a control or notice remains
  the model's assessment. Code does not restore entries the model rejected
  through keyword, mailto or candidate-ranking fallbacks.
- **Status:** a new fetch/render supersedes the previous response for its
  URL, including redirects. Classify each observation using its own HTTP
  status, authority and evidence. Prefer official sources; within that
  tier closed/expired evidence wins, then the observation carrying the
  verified apply entry, then the latest. A claim cited on an observation
  that a later fetch or render of the same URL superseded counts only if
  the superseding observation shows it too; otherwise it is rejected with
  that reason. HTTP 404/410 is closed; other failed responses are unknown.
  On a successful response, a closed notice or past JSON-LD `validThrough`
  wins over an apply entry. Only official sources can yield OPEN. A stale
  board cannot close an observed official posting; its HTTP 200 cannot
  promote a failed official fetch. HTTP 200 alone proves nothing.
- **Failure:** provider refusals, transport failures, exhausted schema
  repairs and unavailable rendering are recorded. Without a completed
  reading there is no inferred apply entry. HTTP and JSON-LD facts still
  apply; otherwise status stays unknown and extracted text remains usable.
  Transport failures are not schema failures: a provider outage during
  the reading propagates and the Activity retries, as the evaluator's
  transport errors do, rather than persisting a verification the outage
  produced.

The optional renderer uses Playwright with a configured Chromium binary.
It records the final document's actual URL, HTTP status and redirect chain.
DNS is pinned to vetted public addresses for the page host and referenced
subdomains of that exact host. Other hosts, non-GET/HEAD requests, service
workers and WebSockets are blocked. This deliberately does not guess a
registrable domain or add CDN/site exceptions; blocked dependencies can
leave rendering incomplete. The Compose image installs Chromium through
Playwright and points `COUNTERSIGN_CHROMIUM_PATH` at it; host runs name
their own binary or leave rendering unavailable.

Every observation is stored with provenance. Snapshot identity includes
source URLs, HTTP status and content, so identical bytes from different
sources do not collapse into one record. The persisted reading carries
observation references, accepted/rejected claims, steps, stop reason,
prompt version and returned usage. Verification binds to the observation
that governs status. Evaluation and its salary-source stamp use the chosen
posting body; approval pins that body and the verification separately.

## Consequences

- One generic reading path replaces phrase lists and duplicate candidate
  ranking. The unscripted fake reader returns an empty assessment; tests
  needing an open posting explicitly script its reading.
- A dossier lists at most 120 controls per observation, one per target,
  in document order, and says when it was cut (`controls_truncated`).
  This is a size cap, not a keyword filter; link-heavy board pages carried
  150+ controls and several thousand tokens per observation without it.
- Up to six provider attempts per reading (three steps plus one repair
  each). Usage counts returned responses; failed requests may expose no
  token counts and are not assumed free.
- The model's stated reason for each step and its notes on the final
  reading are persisted with the reading, so an empty reading can be
  audited without re-running it; `countersign.diagnostics.replay_reading`
  replays a stored observation through the reader when that is needed.
- Company-domain verification, board authority classification and permission
  to approve from a board remain separate policy work. This change does
  not grant new official-source tiers.
- Adding a move with side effects still requires a new ADR. ATS adapters
  and human attestation are separate decisions, not implicit fallbacks.
- Updating v4 §12 / §15 to describe this reader remains owner-reserved.
