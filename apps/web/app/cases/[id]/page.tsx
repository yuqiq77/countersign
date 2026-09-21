"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  getCase,
  submitDecision,
  type Attestation,
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

// States the workflow can still move on its own; anything else stops polling.
const IN_FLIGHT_STATES = new Set([
  "DISCOVERED",
  "SNAPSHOTTED",
  "NORMALIZED",
  "DEDUPLICATED",
  "OFFICIAL_SOURCE_VERIFIED",
  "EVALUATED",
  "PENDING_APPROVAL",
]);

// What each verification status means for the reviewer. The enum value stays
// visible beside it; the recorded reason follows on the next line.
const STATUS_COPY: Record<string, string> = {
  OPEN: "Open — application entry confirmed on an official source",
  OPEN_THIRD_PARTY_ONLY: "Open on a non-official source only",
  UNVERIFIED: "Not verified — no application entry confirmed on an official source",
  CLOSED: "Closed",
  EXPIRED: "Expired",
};

// A closed or expired posting is never attestable (ADR-013); the workflow
// closes such cases before approval, so this is a guard, not a path.
const NEVER_ATTESTABLE = new Set(["CLOSED", "EXPIRED"]);

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
    label: "Request evidence",
    className:
      "rounded-xl border border-neutral-600 px-5 py-2 font-medium disabled:opacity-50",
  },
};

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [reason, setReason] = useState("");
  const [attest, setAttest] = useState(false);
  const [attestNote, setAttestNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (document.hidden) return;
    try {
      setDetail(await getCase(params.id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [params.id]);

  const inFlight = detail === null || IN_FLIGHT_STATES.has(detail.state);

  useEffect(() => {
    void refresh();
    if (!inFlight) return; // decided/quarantined/closed cases cannot change
    const timer = setInterval(() => void refresh(), 4000);
    return () => clearInterval(timer);
  }, [refresh, inFlight]);

  async function decide(decision: string, attestation?: Attestation) {
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
        attestation,
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

  const evaluation = detail.evaluation?.evaluation;
  const canDecide =
    detail.state === "PENDING_APPROVAL" && detail.approval?.status === "PENDING";
  const approveAllowed = detail.approval?.allowed_decisions.includes("APPROVE") ?? false;
  const attestable =
    canDecide &&
    !approveAllowed &&
    detail.verification !== null &&
    !NEVER_ATTESTABLE.has(detail.verification.status);

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

      {evaluation && detail.evaluation ? (
        <section className="rounded-2xl border border-neutral-800 p-5">
          <h2 className="mb-3 font-medium text-neutral-300">
            Evaluation{" "}
            <span className="ml-2 text-xs text-neutral-500">
              {detail.evaluation.policy_version} · {detail.evaluation.prompt_version} ·{" "}
              {detail.evaluation.usage.model_id} ·{" "}
              {detail.evaluation.usage.input_tokens}/
              {detail.evaluation.usage.output_tokens} tok ·{" "}
              {detail.evaluation.usage.latency_ms} ms
            </span>
          </h2>
          <p className="mb-3 text-neutral-300">
            Decision: <strong>{detail.gated_decision ?? evaluation.decision}</strong>
            {detail.gated_decision && detail.gated_decision !== evaluation.decision ? (
              <span className="ml-1 text-xs text-amber-400">
                (model proposed {evaluation.decision}; gated — status not verified OPEN)
              </span>
            ) : null}{" "}
            · Seniority:{" "}
            {evaluation.seniority_fit} · Compensation:{" "}
            {detail.evaluation.compensation_signal}
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
            Approval · {detail.approval.status}
          </h2>
          {canDecide ? (
            <div className="space-y-3">
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Reason (required — every decision is audited)"
                className="w-full rounded-xl border border-neutral-700 bg-transparent px-4 py-2 outline-none focus:border-emerald-500"
                rows={2}
              />
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
              {!approveAllowed ? (
                <p className="text-xs text-amber-400">
                  Plain approval is gated: no application entry was confirmed on
                  an official source.
                </p>
              ) : null}
              {attestable ? (
                /* ADR-013: the reviewer may sign for an unverified posting
                   themselves. The server re-checks the gate with the
                   attestation and records it as its own audit event. */
                <div className="space-y-2 rounded-xl border border-amber-700 p-4">
                  <label className="flex items-center gap-2 text-neutral-300">
                    <input
                      type="checkbox"
                      checked={attest}
                      onChange={(e) => setAttest(e.target.checked)}
                    />
                    I verified this posting is open myself
                  </label>
                  <input
                    value={attestNote}
                    onChange={(e) => setAttestNote(e.target.value)}
                    disabled={!attest}
                    placeholder="What you saw, e.g. the application form loaded and accepted input today (recorded in the audit log)"
                    className="w-full rounded-xl border border-neutral-700 bg-transparent px-4 py-2 outline-none focus:border-amber-500 disabled:opacity-50"
                  />
                  <button
                    onClick={() =>
                      void decide("APPROVE", {
                        observed_open: true,
                        note: attestNote.trim(),
                      })
                    }
                    disabled={busy || !attest || !attestNote.trim()}
                    className="rounded-xl border border-amber-600 px-5 py-2 font-medium text-amber-300 disabled:opacity-50"
                  >
                    Approve on my own attestation
                  </button>
                  <p className="text-xs text-neutral-500">
                    The decision reason above is still required. The attestation is
                    recorded as a separate human event.
                  </p>
                </div>
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
