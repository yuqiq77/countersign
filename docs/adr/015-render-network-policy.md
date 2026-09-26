# ADR-015: Render network policy — navigation versus resource hosts

- Status: accepted
- Date: 2026-09-26
- Amends: ADR-012 (the renderer's host rule). Everything else in ADR-012
  stands: rendering is model-requested (plus the recorded step-0
  pre-render of a shell), read-only, DNS-pinned, budgeted.

## Context

ADR-012 let a render resolve only the page host and subdomains of that
exact host, to avoid guessing registrable-domain boundaries or keeping a
list of CDN exceptions. On a live ATS posting the rule left the rendered
document as empty as the static shell: the application loads its bundle
from the vendor's asset CDN and its data from a sibling subdomain, and
both were blocked. Rendered with those two hosts allowed, the same page
shows the application link and the apply control, on a host the authority
policy already treats as official. The rule was working as written and
was wrong for the page.

## Decision

Split what a render may do into two questions with two answers.

- **Main-document navigation** keeps the ADR-012 boundary: the page host
  and its subdomains. A script or redirect that tries to move the main
  document elsewhere is aborted; the observation stays attributed to the
  page host, with its real final URL and status.
- **Resource loading** (scripts, data requests, images, fonts,
  stylesheets, sub-frames) may use any host the fetched HTML itself names,
  including hosts named in JSON-escaped inline configuration. The page's
  own references are the allowlist: no registrable-domain guess, no vendor
  or CDN list.
- Unchanged: every permitted host resolves to a vetted public address and
  is pinned for the render; GET and HEAD only; service workers and
  WebSockets blocked; at most ten named hosts per render, taken in document
  order; resolution stops at the reading deadline; the page host is always
  tried and must be public.
- Resource hosts are not observations and carry no authority. The rendered
  document is what the official host serves to a browser after loading
  the resources it chose; that is the evidence a human would see.

## Consequences

- The blocked ATS case renders its application UI; with an official ATS
  host, the existing citation and classification path can yield OPEN.
- A host that only a script discovers at run time (named in a bundle, not
  in the HTML) stays blocked. Revisit on real cases, not by widening the
  rule in advance.
- The worst case of the widening is a handful of read-only GETs from the
  worker to public hosts a page chose to name, in a credential-free
  browser context — which FOLLOW already permits for any listed href.
- The guard now classifies requests; a request whose frame is unknown is
  treated as a main-document navigation (the stricter rule). When a script
  or redirect does try to move the main document off the page host family,
  the browser is left on an error page and the render fails with that
  reason ("main document left the page host"), recorded as the step's
  outcome; the pre-navigation DOM is not reported as an observation.
- Known limit, verified 2026-09-26 with a local server: the route guard
  sees the first request of an HTTP redirect chain only; the browser
  follows a 302 internally. A main document that a redirect sends to a
  resource host is therefore not aborted in flight — the resource host
  receives that one GET (a request it is already permitted to receive as
  a resource) and the render then fails on the final-host check with the
  same reason. No observation is produced and no host outside the named
  set is reachable. A browser test for this shape is a follow-up; a
  rework that fetches the main document outside the browser was tried
  and reverted as out of proportion.

## Rejected alternatives

- **Allow the registrable domain of the page host.** Rejected: it guesses
  a boundary (public-suffix rules) and would still miss the vendor CDN.
- **A vendor allowlist.** Rejected: a list to maintain, and a guess about
  which vendors matter; the page already says which hosts it needs.
- **Wait for the ATS adapter (Slice 2).** Rejected as the only path: the
  adapter is still the better long-term answer for that vendor, but the
  renderer's rule was mis-sized for ordinary client-rendered pages.
