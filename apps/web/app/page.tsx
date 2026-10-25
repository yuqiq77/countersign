"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createCase,
  getBrief,
  listCandidates,
  listCases,
  type BriefView,
  type CandidateView,
  type CaseGroup,
  type CaseSummary,
} from "../lib/api";

// The server derives each case's group from stored records; a view only
// chooses which groups it shows.
const VIEWS: { key: string; label: string; groups: CaseGroup[] }[] = [
  { key: "active", label: "Active", groups: ["AWAITING_DECISION", "IN_PROGRESS"] },
  { key: "approved", label: "Approved", groups: ["APPROVED"] },
  { key: "rejected", label: "Rejected", groups: ["REJECTED"] },
  { key: "expired", label: "Expired without decision", groups: ["EXPIRED"] },
  { key: "closed", label: "Closed at source", groups: ["SOURCE_CLOSED"] },
  { key: "stopped", label: "Stopped", groups: ["STOPPED"] },
];

// Discovery (ADR-017): what the screen dropped, what the system does not
// fetch on its own, and what the search may know of the profile.
const DISCOVERY_VIEWS: { key: string; label: string; status?: "DROP" | "LEAD" }[] = [
  { key: "screened", label: "Screened out", status: "DROP" },
  { key: "leads", label: "Leads", status: "LEAD" },
  { key: "brief", label: "Search brief" },
];

// The evaluator's decision bands in the order the policy lists them, best
// first; a case without an evaluation sorts after all of them.
const BANDS = ["APPLY_NOW", "STRETCH", "WAIT", "NETWORK", "BENCHMARK", "SKIP"];

const BAND_STYLE: Record<string, string> = {
  APPLY_NOW: "border-emerald-500 bg-emerald-500/15 text-emerald-300",
  STRETCH: "border-sky-600 bg-sky-500/10 text-sky-300",
  SKIP: "border-neutral-800 text-neutral-500",
};

function bandRank(c: CaseSummary): number {
  const rank = c.gated_decision === null ? -1 : BANDS.indexOf(c.gated_decision);
  return rank === -1 ? BANDS.length : rank;
}

function due(c: CaseSummary): number {
  return c.approval_expires_at === null ? Infinity : Date.parse(c.approval_expires_at);
}

// Best band first; within a band, the decision due soonest, then the most
// recently updated.
function byBand(a: CaseSummary, b: CaseSummary): number {
  return (
    bandRank(a) - bandRank(b) ||
    due(a) - due(b) ||
    Date.parse(b.updated_at) - Date.parse(a.updated_at)
  );
}

function dueIn(iso: string): { text: string; soon: boolean } {
  const ms = Date.parse(iso) - Date.now();
  // Past the deadline, the workflow's timer has yet to close the request.
  if (ms <= 0) return { text: "expiring now", soon: true };
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return { text: "under 1 h", soon: true };
  const text = hours < 24 ? `${hours} h` : `${Math.floor(hours / 24)} d ${hours % 24} h`;
  return { text, soon: hours < 24 };
}

function CaseTable({
  cases,
  showDue,
  showState,
  empty,
}: {
  cases: CaseSummary[];
  showDue?: boolean;
  showState?: boolean;
  empty: string;
}) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-neutral-400">
        <tr>
          <th className="pb-2 pr-4 font-medium">Recommendation</th>
          <th className="pb-2 pr-4 font-medium">Title</th>
          {showState ? <th className="pb-2 pr-4 font-medium">State</th> : null}
          <th className="pb-2 pr-4 font-medium">Company</th>
          <th className="pb-2 font-medium">{showDue ? "Decide within" : "Updated"}</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((c) => {
          const skip = c.gated_decision === "SKIP";
          const deadline =
            showDue && c.approval_expires_at !== null ? dueIn(c.approval_expires_at) : null;
          return (
            <tr key={c.case_id} className="border-t border-neutral-800">
              <td className="py-3 pr-4">
                {c.gated_decision === null ? (
                  <span className="text-neutral-600">—</span>
                ) : (
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs ${
                      BAND_STYLE[c.gated_decision] ?? "border-neutral-600 text-neutral-300"
                    }`}
                  >
                    {c.gated_decision}
                  </span>
                )}
              </td>
              <td className="py-3 pr-4">
                <Link
                  href={`/cases/${c.case_id}`}
                  className={`hover:underline ${skip ? "text-neutral-500" : "text-emerald-400"}`}
                >
                  {c.title ?? c.url}
                </Link>
                {c.screen_failed ? (
                  <span
                    title="Admitted although its coarse screen failed"
                    className="ml-2 rounded border border-amber-700 px-1.5 py-0.5 text-[10px] text-amber-400"
                  >
                    screen failed
                  </span>
                ) : null}
              </td>
              {showState ? (
                <td className="py-3 pr-4">
                  <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">
                    {c.state}
                  </span>
                </td>
              ) : null}
              <td className={`py-3 pr-4 ${skip ? "text-neutral-500" : "text-neutral-300"}`}>
                {c.company_hint ?? "—"}
              </td>
              <td className="py-3 text-neutral-500">
                {deadline ? (
                  <span className={deadline.soon ? "text-amber-400" : undefined}>
                    {deadline.text}
                  </span>
                ) : (
                  new Date(c.updated_at).toLocaleString()
                )}
              </td>
            </tr>
          );
        })}
        {cases.length === 0 ? (
          <tr>
            <td colSpan={showState ? 5 : 4} className="py-8 text-center text-neutral-500">
              {empty}
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

function CandidateTable({
  candidates,
  started,
  onStart,
  empty,
}: {
  candidates: CandidateView[];
  started: Set<string>;
  onStart: (c: CandidateView) => void;
  empty: string;
}) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-neutral-400">
        <tr>
          <th className="pb-2 pr-4 font-medium">Title</th>
          <th className="pb-2 pr-4 font-medium">Company</th>
          <th className="pb-2 pr-4 font-medium">Found by</th>
          <th className="pb-2 pr-4 font-medium">Reason</th>
          <th className="pb-2 font-medium" />
        </tr>
      </thead>
      <tbody>
        {candidates.map((c) => (
          <tr key={c.key} className="border-t border-neutral-800 align-top">
            <td className="py-3 pr-4">
              <a href={c.url} target="_blank" rel="noreferrer" className="hover:underline">
                {c.title}
              </a>
            </td>
            <td className="py-3 pr-4 text-neutral-300">{c.company ?? "—"}</td>
            <td className="py-3 pr-4 text-neutral-500">
              {c.source === "BOARD" ? c.origin : `search: ${c.origin || "—"}`}
            </td>
            <td className="py-3 pr-4 text-neutral-400">{c.reason ?? "—"}</td>
            <td className="py-3 text-right">
              <button
                type="button"
                disabled={started.has(c.url)}
                onClick={() => onStart(c)}
                className="whitespace-nowrap rounded-lg border border-neutral-700 px-3 py-1 text-xs text-neutral-300 hover:border-emerald-500 disabled:opacity-50"
              >
                {started.has(c.url) ? "Started" : "Start case"}
              </button>
            </td>
          </tr>
        ))}
        {candidates.length === 0 ? (
          <tr>
            <td colSpan={5} className="py-8 text-center text-neutral-500">
              {empty}
            </td>
          </tr>
        ) : null}
      </tbody>
    </table>
  );
}

function BriefPanel({ brief }: { brief: BriefView | null }) {
  if (brief === null) {
    return <p className="py-8 text-center text-sm text-neutral-500">No brief derived yet.</p>;
  }
  const lists: [string, string[]][] = [
    ["Role families", brief.brief.role_families],
    ["Skills", brief.brief.skills],
    ["Regions", brief.brief.regions],
    ["Languages", brief.brief.languages],
    ["Roles to exclude", brief.brief.exclude_roles],
  ];
  return (
    <div className="text-sm">
      <p className="mb-4 text-neutral-400">
        The search sees only this brief, derived from your profile; its queries cannot carry
        anything else. Derived {new Date(brief.created_at).toLocaleString()} by{" "}
        {brief.model_route} ({brief.prompt_version}), profile {brief.profile_fingerprint}.
      </p>
      <dl className="grid grid-cols-[10rem_1fr] gap-y-2">
        {lists.map(([label, entries]) => (
          <div key={label} className="contents">
            <dt className="text-neutral-400">{label}</dt>
            <dd className="text-neutral-200">{entries.length ? entries.join(", ") : "—"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default function HomePage() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [view, setView] = useState("active");
  const [candidates, setCandidates] = useState<CandidateView[] | null>(null);
  const [brief, setBrief] = useState<BriefView | null | undefined>(undefined);
  const [started, setStarted] = useState<Set<string>>(new Set());
  // The refresh timer reads the current view through this, not a stale closure.
  const viewRef = useRef("active");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One idempotency key per submission intent: a retry after a timeout or
  // error replays the same key; only a successful create mints a new one.
  const intentKey = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (document.hidden) return;
    try {
      setCases(await listCases());
      setLoaded(true);
      const discovery = DISCOVERY_VIEWS.find((v) => v.key === viewRef.current);
      if (discovery?.status) setCandidates(await listCandidates(discovery.status));
      else if (discovery) setBrief(await getBrief());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // The chosen view lives in the URL, so returning from a case keeps it. It
  // is read before the first refresh, which then loads that view's data.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("view");
    const match = [...VIEWS, ...DISCOVERY_VIEWS].find((v) => v.key === wanted);
    if (match) {
      setView(match.key);
      viewRef.current = match.key;
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  function choose(key: string) {
    setView(key);
    viewRef.current = key;
    setCandidates(null);
    window.history.replaceState(null, "", key === "active" ? "/" : `/?view=${key}`);
    void refresh();
  }

  async function startCase(candidate: CandidateView) {
    // Disabled while the request is in flight. The key belongs to the
    // candidate, not to the click: a retry after an error, a lost response
    // or a reload replays it, and the API starts the workflow a failed first
    // request left unstarted instead of creating a second case.
    setStarted((prior) => new Set(prior).add(candidate.url));
    try {
      await createCase(candidate.url, `candidate-${candidate.id}`);
      await refresh();
    } catch (e) {
      setStarted((prior) => {
        const next = new Set(prior);
        next.delete(candidate.url);
        return next;
      });
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    if (intentKey.current === null) {
      intentKey.current = crypto.randomUUID();
    }
    try {
      await createCase(url.trim(), intentKey.current);
      intentKey.current = null;
      setUrl("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const inGroups = (groups: CaseGroup[]) =>
    cases.filter((c) => groups.includes(c.group)).sort(byBand);
  const current = VIEWS.find((v) => v.key === view) ?? VIEWS[0];
  const discoveryView = DISCOVERY_VIEWS.find((v) => v.key === view);
  const awaiting = inGroups(["AWAITING_DECISION"]);
  const inProgress = inGroups(["IN_PROGRESS"]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Countersign</h1>
        <p className="mt-1 text-sm text-neutral-400">
          Paste an official job URL. The durable workflow snapshots, verifies,
          evaluates, and waits for your signature.
        </p>
      </header>

      <form onSubmit={onSubmit} className="mb-8 flex gap-3">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://boards.greenhouse.io/…"
          className="flex-1 rounded-xl border border-neutral-700 bg-transparent px-4 py-2 text-sm outline-none focus:border-emerald-500"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-xl bg-emerald-600 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Starting…" : "Create case"}
        </button>
      </form>

      {error ? (
        <p className="mb-4 rounded-lg border border-red-800 px-4 py-2 text-sm text-red-400">
          {error}
        </p>
      ) : null}

      <nav className="mb-6 flex flex-wrap gap-2 text-sm">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            type="button"
            aria-pressed={!discoveryView && v.key === current.key}
            onClick={() => choose(v.key)}
            className={`rounded-full border px-3 py-1 ${
              !discoveryView && v.key === current.key
                ? "border-emerald-500 text-emerald-300"
                : "border-neutral-700 text-neutral-400 hover:text-neutral-200"
            }`}
          >
            {v.label}
            <span className="ml-1.5 text-neutral-500">
              {cases.filter((c) => v.groups.includes(c.group)).length}
            </span>
          </button>
        ))}
        <span className="mx-1 self-center text-neutral-700">|</span>
        {DISCOVERY_VIEWS.map((v) => (
          <button
            key={v.key}
            type="button"
            aria-pressed={v.key === discoveryView?.key}
            onClick={() => choose(v.key)}
            className={`rounded-full border px-3 py-1 ${
              v.key === discoveryView?.key
                ? "border-emerald-500 text-emerald-300"
                : "border-neutral-700 text-neutral-400 hover:text-neutral-200"
            }`}
          >
            {v.label}
            {v.key === discoveryView?.key && v.status && candidates !== null ? (
              <span className="ml-1.5 text-neutral-500">{candidates.length}</span>
            ) : null}
          </button>
        ))}
      </nav>

      {discoveryView ? (
        discoveryView.status ? (
          candidates === null ? null : (
            <CandidateTable
              candidates={candidates}
              started={started}
              onStart={(c) => void startCase(c)}
              empty={
                discoveryView.status === "DROP"
                  ? "Nothing screened out in the last 30 days."
                  : "No leads in the last 30 days."
              }
            />
          )
        ) : brief === undefined ? null : (
          <BriefPanel brief={brief} />
        )
      ) : !loaded ? null : cases.length === 0 ? (
        <p className="py-8 text-center text-sm text-neutral-500">No cases yet.</p>
      ) : current.key === "active" ? (
        <>
          <h2 className="mb-2 text-sm font-medium text-neutral-300">Awaiting your decision</h2>
          <CaseTable cases={awaiting} showDue empty="Nothing awaits your decision." />
          {inProgress.length > 0 ? (
            <>
              <h2 className="mb-2 mt-10 text-sm font-medium text-neutral-300">In progress</h2>
              <CaseTable cases={inProgress} showState empty="" />
            </>
          ) : null}
        </>
      ) : (
        <CaseTable
          cases={inGroups(current.groups)}
          showState={current.key === "stopped"}
          empty="Nothing here."
        />
      )}
    </main>
  );
}
