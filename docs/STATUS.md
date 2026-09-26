# Countersign — Status

Current focus, blockers, next steps, and owner decisions. Rewritten when
these change; history lives in git and ADRs. Private live-run details stay
in the gitignored `HANDOFF.md`.

| Field | Value |
| --- | --- |
| As of | 2026-09-26 |
| Branch | `main`, no remote. Redirect cookie isolation: `7680df6`; render network policy: `32567c5`; reader evidence fixes: `3d3377e`; prior-assessment input and round history: `6e8b81c`. Documentation committed separately. |
| Slice | Slice 1 (one durable case): code complete; exit milestone (Track A) observed 2026-09-18; worker-kill recovery repeated 2026-09-19. |
| Current focus | Redirect cookie isolation is committed and automatically verified. Resolve the salary-period gap and reader citation failures; salary-note isolation, attestation, polling and round paging have live evidence. |

## Current implementation

- **HTTP fetch:** cookies are scoped to one fetch's redirect chain and
  matched against each hop's logical URL. The shared HTTP client's jar
  neither stores nor sends cookies, including across concurrent
  Activities sharing a pinned address. Each hop still resolves and
  validates its destination before connecting to the vetted address.
  Host-only cookie matching retains the documented subdomain limitation.
- **Reader (ADR-012):** bounded FOLLOW/RENDER loop inside `verify_status`;
  at most 120 controls per observation (exact label/target duplicates
  merged, document order), text up to 16,000 characters and beyond that
  the verbatim head and tail with a marked gap (`text_truncated`), 120
  seconds after the initial fetch, three model steps, two follows and one
  schema repair per step. `page-reader/0.4.0` requires an explicit
  apply-entry choice, including `NONE`; reasons and notes are persisted.
  Replay a stored observation with
  `python -m countersign.diagnostics.replay_reading <case or snapshot id>`.
  JSON-LD citations are checked against observed fields: `directApply`
  must be true and its key/value pair cited with quoting/spacing tolerance
  and word boundaries; `validThrough` must be nonempty and its full value
  included in the citation. A model-cited expiry prevents a redundant
  code-added expiry for that observation. The sanitized company-page
  fixture covers late application instructions and differently labelled
  links sharing a mailto target.
- **Rendering:** a page with no visible HTML text and no controls gets a
  recorded step-0 pre-render, including JSON-LD-only shells. Docker ships
  Chromium; host runs use `COUNTERSIGN_CHROMIUM_PATH`. Render network
  policy (ADR-015, implemented in `32567c5`): the main document stays on
  the page host and its subdomains; resources may load from hosts the
  fetched HTML names (at most ten, document order, DNS-pinned, GET/HEAD).
  This replaced the subdomain-only rule after a live ATS shell rendered
  empty because its bundle CDN and data host were blocked; replaying that
  stored case under the new policy cited the apply control on the
  official ATS host and classified OPEN. Provider outages retry the
  Activity; they do not become verification verdicts.
- **Provenance:** control labels and targets are checked together; claims
  on superseded pages must hold on their replacement. Verification pins
  the governing source, which supplies the posting body for evaluation,
  salary-source stamping and case detail. Model claims do not override
  authority classification or the approval gate.
- **Approval (ADR-013):** plain APPROVE requires verified OPEN. UNVERIFIED
  and OPEN_THIRD_PARTY_ONLY can be approved with a typed human attestation
  checked against the request's pinned verification and audited. CLOSED,
  EXPIRED and missing verification cannot be attested.
- **Evidence loop (ADR-014, [ticket 001](tickets/001-evidence-loop.md)):**
  REQUEST_EVIDENCE stores the reviewer's reason and continues the same
  `job-case/{case_id}` workflow into the next generation. Later rounds
  skip intake/normalization, re-observe and re-evaluate, then create a new
  approval; three rounds maximum. Signals must match the current round.
  `evaluator/0.3.1` receives all prior REQUEST_EVIDENCE reasons as labelled
  operator context and, from round 2, the previous round's structured
  evaluation as a labelled revisable judgment (ADR-014 amendment): the
  current posting, profile and context remain the basis, overturning is
  allowed, and candidate facts the earlier round inferred are not facts.
  The previous assessment comes from the preceding approval's pinned
  evaluation, including on Activity retries. Records carry their round,
  the decision ids consumed and the previous evaluation id; records lacking
  a round recover it from the pinning approval or their historical position.
  The `summary` field states its purpose in the contract
  (decision and reasons for the reviewer; what context or the previous
  assessment resolved and what remains open; no restated rules). The
  reader receives no reviewer instructions.
- **Round presentation:** case detail returns every round's evaluation;
  the page offers ‹ › navigation (latest by default, earlier rounds
  marked superseded and shown with the context they saw; the gated
  decision applies to the latest only). It shows supplied context and
  the approval round; earlier rounds are labelled as the model's
  recommendation of that round, since the gate result of a past round is
  not stored; polling uses server-derived `in_flight`. An
  atomic `case.round.continued` marker distinguishes an active continuation
  from REQUEST_EVIDENCE decisions that ended cases before ADR-014. Intake
  replays start a workflow only for cases still in DISCOVERED.
- **Schema:** unreleased migration `0001` remains the schema baseline.
  Upgrading from before ADR-013 requires fresh schema/history; the later
  reader diagnostics and evidence loop add no migration. The workflow-id
  cutover requires no pending approvals addressed to old suffixed ids.

## Verification and deployment

Code commit `3d3377e` was checked on 2026-09-26 in a fresh detached
worktree and Python 3.14.3 venv, with front-end dependencies installed
from `pnpm-lock.yaml`:

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  107 Python files; `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest -ra`: **196 passed, no skips**. PostgreSQL integration used
  disposable database `countersign_reader_code_check` at
  `127.0.0.1:50867` in a separate temporary container. Other database
  fixtures used SQLite; Temporal used its test server; rendering used real
  local Chromium. Private profile and salary inputs were unset; model
  providers were fakes. Coverage includes excerpt truncation, exact
  label/target deduplication, JSON-LD quoting and incorrect-field/value
  rejection, alongside the existing workflow and approval regressions.
- `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed.
- Changed files were checked for operator-specific identifiers,
  credential-like values and private paths; no matches. A full gitleaks
  scan remains a publication prerequisite.

The documentation layer repeated all commands above in a second fresh
detached worktree and venv, using disposable PostgreSQL database
`countersign_reader_docs_check` at the same temporary endpoint:
**196 passed, no skips**, with all static and web checks passing again.

Deployment was not rechecked or changed during this review; no live model
calls were made. Existing saved-page replay evidence remains in the
private handoff.

**Running-stack evidence, 2026-09-25:** read-only PostgreSQL inspection of
an owner-tested case confirmed two retained verifications, evaluations
and approvals under the stable workflow id. Round 1 was decided
REQUEST_EVIDENCE; round 2 was pending. Both evaluations used
`evaluator/0.3.0`, and the second pinned the first decision as context.
The posting content was unchanged; verification remained
OPEN_THIRD_PARTY_ONLY. Exactly one continuation marker existed. No pending
approvals remained on legacy round-suffixed workflow ids. This confirms
the deployed two-round path, not browser polling or every live acceptance
check. No deployment changes or live model calls were made during this
cleanup/commit round.

Code commit `32567c5` (ADR-015 render network policy, on top of the
committed evidence fixes in `3d3377e`) was checked on 2026-09-26 in a
fresh detached worktree and Python 3.14.3 venv, with front-end dependencies
installed from `pnpm-lock.yaml`:

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  108 Python files; `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest -ra`: **209 passed, no skips**. PostgreSQL integration
  used disposable database `countersign_code_32567c5` on the Compose
  PostgreSQL at `127.0.0.1:5435`, dropped afterwards; Temporal used its
  test server and rendering used real local Chromium. Coverage includes
  a named second resource host and refusal of a main-document escape.
- `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed.

The ADR-015 documentation layer repeated all commands above in a second
fresh detached worktree and venv, using disposable PostgreSQL database
`countersign_docs_adr015` at the same endpoint: **209 passed, no skips**,
with all static and web checks passing again. Live checks by the owner on
2026-09-25 after the excerpt/dedupe fix: a company page and a board page
classified OPEN_THIRD_PARTY_ONLY. Replay of the stored ATS shell case under
ADR-015 with real navigation: OPEN. The worker was rebuilt with `3d3377e`
and `32567c5` on 2026-09-26; the subsequent live evidence is recorded below.

Code commit `7680df6` (redirect cookie isolation) was checked on 2026-09-26
in a fresh detached worktree and Python 3.14.3 venv, with front-end
dependencies installed from `pnpm-lock.yaml`:

- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  109 Python files; `.venv/bin/pyright`: 0 errors;
  `.venv/bin/lint-imports`: 3 contracts kept.
- `.venv/bin/pytest -ra`: **213 passed, no skips**, including redirect
  session continuity, domain rejection and concurrent cross-case cookie
  isolation. PostgreSQL integration used disposable database
  `countersign_cookie_code_7680df6` at `127.0.0.1:5435` (dropped after
  verification); other database fixtures used SQLite, Temporal used its
  test server and browser tests used local Chrome. Private profile and
  salary environment inputs were unset; model providers were fakes.
- `pnpm --dir apps/web typecheck` and `pnpm --dir apps/web build`: passed.

The commit verification did not deploy the worker or make live model
calls. The running worker's cookie implementation was not rechecked.

## Next

1. Live acceptance so far (2026-09-25 evening, `evaluator/0.3.1`, three
   cases, two rounds each, two models): the re-evaluation with a reviewer
   note is verified, including notes that were questions rather than
   evidence — each round-2 summary answered the note directly, cited the
   previous evaluation, said what changed and what remains without
   restating rules, and carried no round-1 inference about the candidate
   forward as fact. One case's round-2 summary narrated a ±1 score move
   as a change, which is within the measured sampling noise. On
   2026-09-26, after the rebuild for `3d3377e` and `32567c5` (image built
   13:39 UTC, citation tolerance present in the container), five more
   cases covered the reader paths that had not been hit live: direct ATS
   postings on two ATS hosts read `OPEN` from the static page; a board
   posting whose only entry pointed at an ATS was followed (`FOLLOW`, one
   step, the model's stated reason persisted) and read `OPEN` on the
   followed official page; a client-rendered ATS shell got the recorded
   step-0 pre-render and read `OPEN` from the rendered document; a company
   careers page stayed `OPEN_THIRD_PARTY_ONLY` as the authority policy
   intends; one case ran a second round on a question note with a plain,
   attributed summary. Later on 2026-09-26 the owner covered three more
   checks: a board posting with a note stating an expected salary left the
   extraction empty and compensation UNKNOWN across three rounds; an
   attested approval on a company careers case recorded the decision and
   its `human.verification_attested` event; the page kept polling and
   paging through three rounds. Three board cases also produced the
   findings in item 2.
2. Reader citation rigidity (three live rejections on board pages, all
   with the fact present in the observation): the dossier shows JSON-LD
   fields under contract names (`direct_apply`) while the rule and prompt
   demand the source spelling (`directApply: true`), so a model that
   quoted the dossier verbatim was rejected — a code bug; a citation of
   the key without its value was rejected by design; and a closed notice
   cited from a followed application page was rejected although the text
   matches. A non-null `href` that is not a control would explain the
   latter rejection; the raw proposal was not stored, so that remains
   unconfirmed. No rejection feedback reached the model. The JSON-LD
   readings ended UNVERIFIED; the closed-notice case retained its board
   entry and ended OPEN_THIRD_PARTY_ONLY, then entered QUARANTINED after
   the evaluator's contract failure. The closed notice itself was an
   artefact of ours, found by the owner and fixed in `7680df6`: the fetcher
   pins each hop to the vetted address, so the transport request names
   an IP literal and the client's cookie jar rejected the ATS's `Domain=`
   session cookie set on a 302; the token
   URL then answered "application not found" (1,440 chars) where a
   browser gets the application form (45,647 chars). Cookies are now kept
   per fetch in a jar keyed by the logical hostname and sent on each hop;
   the shared client is built with a jar that neither stores nor sends,
   because the owner reproduced the earlier clear-around-each-hop version
   leaking one case's session cookie to another site on the same pinned
   address under concurrent Activities. A deterministic two-thread test
   covers that interleaving; the pinning is unchanged. Verified by
   refetching the same entry through the fixed pinned path.
   Proposed, owner's call: (a) show JSON-LD fields under their source
   names in the dossier and accept the underscore form in the rule; (b)
   a bounded citation repair — after a DONE with rejected claims, and
   while the step budget allows, record the rejections as the step's
   outcome and ask once more, so the model can re-cite or say NONE; (c)
   store rejected claims structurally (kind, text, observation, href),
   not as strings, so a rejection can be diagnosed from the record.
3. Decide the salary-period fix (below, remaining gaps): a monthly figure
   is currently compared to the annual bands. Keep checking more than one
   model for anchoring on an earlier inference about the candidate as
   rounds accumulate.
4. Before publication, run gitleaks and inspect tracked content. The owner
   adds the remote and pushes.
5. Slice 2 retains the v4 §24 timebox: policy watchlist and official-domain
   mapping, ATS adapters, scans and stale rechecks, capability/route
   registries, calibration, traces and cost dashboard. Tickets are
   owner-read before implementation. Slice 2.5 research follows ADR-011
   once registry prerequisites exist.

## Remaining gaps and owner decisions

- Company pages still require official-domain mapping; several boards
  fall into COMPANY_CONTROLLED and TRUSTED_BOARD_REDIRECT is not assigned.
  Allowing board evidence alone to permit plain APPROVE is a policy decision.
  A button without an href can establish an application entry but cannot
  be FOLLOWed; RENDER does not click it. Slice 2's domain mapping and ATS
  adapters do not promise generic JS-button navigation. Additional reader
  actions require a new ADR; attestation remains the current approval path.
- Cross-host script dependencies can prevent complete rendering;
  synchronous DNS/library calls are not forcibly cancelled by the reading
  budget. Citation checks establish provenance, not semantic correctness.
- Attestation is stored in the decision row and audit log, not displayed
  on the case page. Quarantine/needs-evidence state reasons remain in the
  audit log; verification reasons are displayed.
- Rubric decisions remain: no-profile score nullability, hard blockers and
  decision bands, pay-level risk wording, technical versus coordination
  work. Private operational notes remain in `HANDOFF.md`.
- One observation from the amended live rounds, owner's call whether to
  act: the summary follows the language of the reviewer's note (a note in
  another language produced a summary in that language). The board case's
  status change between rounds was not reader variance but the
  pre-`3d3377e` citation rejection; later runs also exposed the separate
  citation failures recorded in Next, item 2.
- ADR-015 known limit (verified 2026-09-26): an HTTP redirect of the main
  document to a named resource host is not aborted in flight — the browser
  follows redirects without consulting the route guard — so that host
  receives one GET before the render fails on the final-host check. No
  observation results and no unnamed host is reachable. A browser test for
  this shape is a follow-up; the main-document-outside-the-browser rework
  was reverted as out of proportion.
- **Host-only cookies reach subdomains.** The per-fetch jar uses
  Python's cookiejar policy, which sends a cookie set without `Domain=` to
  subdomains of the setting host as well as the host itself. Accepted as
  a known laxness for now; a stricter policy is a small follow-up.
- **The relay does not appear to enforce structured outputs.** A board
  case was quarantined after the evaluator's output failed the contract
  twice with a list where an object is required, something a
  grammar-constrained response cannot produce; the worker was routed
  through the relay at the time. The quarantine path worked as designed,
  but live acceptance of contract-shaped behaviour should run on the
  direct API, and a relay result carries no structured-output guarantee.
- **Salary period is not modelled.** A live ATS posting stated a monthly
  CHF figure; the model extracted it as `min_amount` and
  `compute_compensation_signal` compared it to the annual bands, giving
  CONCERNING for a salary that annualised would not be. `SalaryExtraction`
  has no period field and the signal assumes annual amounts. Proposed:
  add `period` (`ANNUAL | MONTHLY | HOURLY | UNKNOWN`) to the extraction,
  annualise monthly by twelve in the signal and return UNKNOWN for hourly
  or unknown periods; that changes the evaluator's output schema (prompt
  version bump) and a policy computation, so it is the owner's call.
- Owner-reserved v4 edits: §9 needs-evidence edge; §12 / §14 old status name;
  §12 / §15 reader; §15 / §24 / §29 research loop when Slice 2.5 nears.
