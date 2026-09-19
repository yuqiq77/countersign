"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { createCase, listCases, type CaseSummary } from "../lib/api";

export default function HomePage() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
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
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 4000);
    return () => clearInterval(timer);
  }, [refresh]);

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

      <table className="w-full text-left text-sm">
        <thead className="text-neutral-400">
          <tr>
            <th className="pb-2 pr-4 font-medium">Title</th>
            <th className="pb-2 pr-4 font-medium">State</th>
            <th className="pb-2 pr-4 font-medium">Company</th>
            <th className="pb-2 font-medium">Updated</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.case_id} className="border-t border-neutral-800">
              <td className="py-3 pr-4">
                <Link
                  href={`/cases/${c.case_id}`}
                  className="text-emerald-400 hover:underline"
                >
                  {c.title ?? c.url}
                </Link>
              </td>
              <td className="py-3 pr-4">
                <span className="rounded-full border border-neutral-700 px-2 py-0.5 text-xs">
                  {c.state}
                </span>
              </td>
              <td className="py-3 pr-4 text-neutral-300">{c.company_hint ?? "—"}</td>
              <td className="py-3 text-neutral-500">
                {new Date(c.updated_at).toLocaleString()}
              </td>
            </tr>
          ))}
          {loaded && cases.length === 0 ? (
            <tr>
              <td colSpan={4} className="py-8 text-center text-neutral-500">
                No cases yet.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </main>
  );
}
