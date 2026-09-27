"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  getCase,
  submitDecision,
  type CaseDetail,
} from "../../../lib/api";

const SCORE_KEYS = [
  ["skill_capital", "Skill capital"],
  ["strategic_alignment", "Strategic alignment"],
  ["learning_velocity", "Learning velocity"],
  ["drift_risk", "Drift risk"],
  ["domain_lock_in_risk", "Domain lock-in risk"],
  ["transition_feasibility", "Transition feasibility"],
] as const;

// What each verification status means for the reviewer. The enum value stays
// visible beside it; the recorded reason follows on the next line.
const STATUS_COPY: Record<string, string> = {
  OPEN: "Open — application entry confirmed on an official source",
  OPEN_THIRD_PARTY_ONLY: "Open on a non-official source only",
  UNVERIFIED: "Not verified — no application entry confirmed on an official source",
  CLOSED: "Closed",
  EXPIRED: "Expired",
};

const DECISION_LABELS: Record<string, { label: string; className: string }> = {
  APPROVE: {
    label: "Approve",
    className:
      "rounded-xl bg-emerald-600 px-5 py-2 font-medium text-white disabled:opacity-50",
  },
  REJECT: {
    label: "Reject",
    className:
      "rounded-xl bg-red-700 px-5 py-2 font-medium text-white disabled:opacity-50",
  },
  REQUEST_EVIDENCE: {
    label: "Add context and re-evaluate",
    className:
      "rounded-xl border border-neutral-600 px-5 py-2 font-medium disabled:opacity-50",
  },
};

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Which round's evaluation is shown; null means the latest, and a new
  // round arriving snaps the view back to it.
  const [roundIndex, setRoundIndex] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    if (document.hidden) return;
    try {
      setDetail(await getCase(params.id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [params.id]);

  // The server decides whether the workflow may still move the case; the
  // evidence-loop re-round passes through NEEDS_MORE_EVIDENCE, so a state
  // list here would stop polling exactly when the next round is coming.
  const inFlight = detail === null || detail.in_flight;

  useEffect(() => {
    void refresh();
    if (!inFlight) return;
    const timer = setInterval(() => void refresh(), 4000);
    return () => clearInterval(timer);
  }, [refresh, inFlight]);

  // A new round brings a new approval request: nothing typed for the
  // previous one carries over.
  const approvalId = detail?.approval?.approval_request_id ?? null;
  useEffect(() => {
    setReason("");
  }, [approvalId]);

  const roundCount = detail?.evaluations.length ?? 0;
  useEffect(() => {
    setRoundIndex(null);
  }, [roundCount]);

  async function decide(decision: string) {
    if (!detail?.approval || !reason.trim()) {
      setError("A reason is required for every decision.");
      return;
    }
    setBusy(true);
    try {
      await submitDecision(
        detail.approval.approval_request_id,
        decision,
        reason.trim(),
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!detail) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-12 text-sm text-neutral-400">
        {error ?? "Loading…"}
      </main>
    );
  }

  const rounds = detail.evaluations;
  const shownIndex = roundIndex ?? rounds.length - 1;
  const shownRound = rounds[shownIndex] ?? null;
  const evaluation = shownRound?.evaluation;
  const isLatestRound = shownIndex === rounds.length - 1;
  const notesSeen = shownRound
    ? detail.operator_context.filter((c) =>
        shownRound.operator_context_ids.includes(c.decision_id),
      )
    : [];
  const canDecide =
    detail.state === "PENDING_APPROVAL" && detail.approval?.status === "PENDING";
  const reEvaluationAllowed =
    detail.approval?.allowed_decisions.includes("REQUEST_EVIDENCE") ?? false;

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-6 py-12 text-sm">
      <div>
        <Link href="/" className="text-xs text-neutral-500 hover:underline">
          ← All cases
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {detail.state === "PENDING_APPROVAL" ? "Awaiting your signature" : detail.state}
        </h1>
        <p className="mt-1 break-all text-neutral-400">{detail.url}</p>
      </div>

      {error ? (
        <p className="rounded-lg border border-red-800 px-4 py-2 text-red-400">{error}</p>
      ) : null}

      <section className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-2xl border border-neutral-800 p-5">
          <h2 className="mb-2 font-medium text-neutral-300">Provenance</h2>
          {detail.snapshot ? (
            <dl className="space-y-1 text-neutral-400">
              <div>Authority: {detail.snapshot.authority_level}</div>
              <div>HTTP: {detail.snapshot.http_status}</div>
              <div>Hash: {detail.snapshot.content_hash.slice(0, 16)}…</div>
              <div>
                Retrieved: {new Date(detail.snapshot.retrieved_at).toLocaleString()}
              </div>
            </dl>
          ) : (
            <p className="text-neutral-500">Not yet snapshotted.</p>
          )}
        </div>

        <div className="rounded-2xl border border-neutral-800 p-5">
          <h2 className="mb-2 font-medium text-neutral-300">Official verification</h2>
          {detail.verification ? (
            <dl className="space-y-1 text-neutral-400">
              <div>
                Status:{" "}
                <span
                  className={
                    detail.verification.status === "OPEN"
                      ? "text-emerald-400"
                      : "text-amber-400"
                  }
                >
                  {STATUS_COPY[detail.verification.status] ?? detail.verification.status}
                </span>{" "}
                <span className="text-xs text-neutral-500">
                  {detail.verification.status}
                </span>
              </div>
              <div>Authority: {detail.verification.authority}</div>
              <div>Reason: {detail.verification.reason}</div>
              <div>
                Checked: {new Date(detail.verification.checked_at).toLocaleString()}
              </div>
              {detail.verification.content_changed ? (
                <div className="text-amber-400">
                  Posting content changed between snapshot and verification.
                </div>
              ) : null}
            </dl>
          ) : (
            <p className="text-neutral-500">Not yet verified.</p>
          )}
        </div>
      </section>

      {evaluation && shownRound ? (
        <section className="rounded-2xl border border-neutral-800 p-5">
          <h2 className="mb-3 flex flex-wrap items-center gap-2 font-medium text-neutral-300">
            <span>
              Evaluation · round {shownRound.generation} of {rounds.length}
            </span>
            {rounds.length > 1 ? (
              /* Every round's judgment is kept; the pager shows earlier ones
                 as they were, never rewritten by a later round. */
              <span className="flex items-center gap-1 text-xs">
                <button
                  onClick={() => setRoundIndex(Math.max(0, shownIndex - 1))}
                  disabled={shownIndex === 0}
                  className="rounded-lg border border-neutral-700 px-2 py-0.5 disabled:opacity-40"
                  aria-label="Previous round"
                >
                  ‹
                </button>
                <button
                  onClick={() => setRoundIndex(Math.min(rounds.length - 1, shownIndex + 1))}
                  disabled={isLatestRound}
                  className="rounded-lg border border-neutral-700 px-2 py-0.5 disabled:opacity-40"
                  aria-label="Next round"
                >
                  ›
                </button>
              </span>
            ) : null}
            <span className="text-xs text-neutral-500">
              {shownRound.policy_version} · {shownRound.prompt_version} ·{" "}
              {shownRound.usage.model_id} · {shownRound.usage.input_tokens}/
              {shownRound.usage.output_tokens} tok · {shownRound.usage.latency_ms} ms
            </span>
          </h2>
          {!isLatestRound ? (
            <p className="mb-3 text-xs text-amber-400">
              Superseded by round {rounds[rounds.length - 1]?.generation}. The model&apos;s
              recommendation is shown as it was; the gate result of that round is not.
            </p>
          ) : null}
          {notesSeen.length > 0 ? (
            <p className="mb-3 text-xs text-neutral-500">
              With your context:{" "}
              {notesSeen.map((c) => `“${c.text}”`).join(" · ")}
            </p>
          ) : null}
          <p className="mb-3 text-neutral-300">
            {isLatestRound ? "Decision" : "Model recommendation"}:{" "}
            <strong>
              {isLatestRound ? (detail.gated_decision ?? evaluation.decision) : evaluation.decision}
            </strong>
            {isLatestRound &&
            detail.gated_decision &&
            detail.gated_decision !== evaluation.decision ? (
              <span className="ml-1 text-xs text-amber-400">
                (model proposed {evaluation.decision}; gated — status not verified OPEN)
              </span>
            ) : null}{" "}
            · Seniority:{" "}
            {evaluation.seniority_fit} · Compensation:{" "}
            {shownRound.compensation_signal}
          </p>
          <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {SCORE_KEYS.map(([key, label]) => (
              <div key={key} className="rounded-lg border border-neutral-800 p-3">
                <div className="text-xs text-neutral-500">{label}</div>
                <div className="text-lg">{evaluation[key]}/10</div>
              </div>
            ))}
          </div>
          {evaluation.hard_blockers.length > 0 ? (
            <div className="mb-2">
              <h3 className="text-xs font-medium uppercase text-red-400">
                Hard blockers
              </h3>
              <ul className="mt-1 list-inside list-disc text-neutral-400">
                {evaluation.hard_blockers.map((b, i) => (
                  <li key={i}>
                    {b.category} ({b.severity}){b.cited_text ? ` — “${b.cited_text}”` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {evaluation.missing_evidence.length > 0 ? (
            <div className="mb-2">
              <h3 className="text-xs font-medium uppercase text-amber-400">
                Missing evidence
              </h3>
              <ul className="mt-1 list-inside list-disc text-neutral-400">
                {evaluation.missing_evidence.map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="text-neutral-400">{evaluation.summary}</p>
        </section>
      ) : null}

      {detail.approval ? (
        <section className="rounded-2xl border border-neutral-800 p-5">
          <h2 className="mb-2 font-medium text-neutral-300">
            Approval · {detail.approval.status} · round {detail.approval.generation} of{" "}
            {detail.approval.max_generations}
          </h2>
          {detail.operator_context.length > 0 ? (
            <div className="mb-3">
              <h3 className="text-xs font-medium uppercase text-neutral-500">
                Context you supplied
              </h3>
              <ul className="mt-1 list-inside list-disc text-neutral-400">
                {detail.operator_context.map((c) => (
                  <li key={c.decision_id}>
                    <span className="text-neutral-500">
                      {new Date(c.supplied_at).toLocaleDateString()}
                    </span>{" "}
                    {c.text}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {canDecide ? (
            <div className="space-y-3">
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Reason (required — every decision is audited)"
                className="w-full rounded-xl border border-neutral-700 bg-transparent px-4 py-2 outline-none focus:border-emerald-500"
                rows={2}
              />
              {reEvaluationAllowed ? (
                <p className="text-xs text-neutral-500">
                  Add context and re-evaluate: your note is recorded as your decision.
                  The posting is read again and evaluated again with your note as
                  operator-supplied context. Facts about you in general belong in
                  your profile.
                </p>
              ) : (
                <p className="text-xs text-neutral-500">
                  Re-evaluation rounds used ({detail.approval.generation} of{" "}
                  {detail.approval.max_generations}): approve or reject.
                </p>
              )}
              {/* The server owns the allowed set (lifecycle gate): render it,
                  never a hardcoded trio. */}
              <div className="flex gap-3">
                {detail.approval.allowed_decisions.map((decision) => {
                  const meta = DECISION_LABELS[decision] ?? {
                    label: decision,
                    className:
                      "rounded-xl border border-neutral-600 px-5 py-2 font-medium disabled:opacity-50",
                  };
                  return (
                    <button
                      key={decision}
                      onClick={() => void decide(decision)}
                      disabled={busy}
                      className={meta.className}
                    >
                      {meta.label}
                    </button>
                  );
                })}
              </div>
              {/* ADR-013 as amended: approving a posting the system did not
                  verify OPEN is the reviewer's own verification, derived and
                  recorded by the server. Nothing is typed to prove it; this
                  line only says so. */}
              {canDecide &&
              detail.verification !== null &&
              detail.verification.status !== "OPEN" ? (
                <p className="text-xs text-neutral-500">
                  Not verified on an official source ({detail.verification.reason}).
                  Approving records that you checked the posting yourself.
                </p>
              ) : null}
            </div>
          ) : (
            <p className="text-neutral-500">
              {detail.approval.decided_at
                ? `Decided ${new Date(detail.approval.decided_at).toLocaleString()}.`
                : `Expires ${new Date(detail.approval.expires_at).toLocaleString()}.`}
            </p>
          )}
        </section>
      ) : null}
    </main>
  );
}
