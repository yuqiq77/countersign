export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface CaseSummary {
  case_id: string;
  state: string;
  url: string;
  title: string | null;
  company_hint: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApprovalRequestView {
  approval_request_id: string;
  case_id: string;
  status: string;
  allowed_decisions: string[];
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  snapshot_id: string | null;
  verification_id: string | null;
  evaluation_id: string | null;
}

export interface EvaluationView {
  evaluation_id: string;
  case_id: string;
  evaluation: {
    decision: string;
    application_status: string;
    skill_capital: number;
    strategic_alignment: number;
    learning_velocity: number;
    drift_risk: number;
    domain_lock_in_risk: number;
    transition_feasibility: number;
    seniority_fit: string;
    hard_blockers: { category: string; severity: string; cited_text: string | null }[];
    missing_evidence: string[];
    risk_flags: { category: string; severity: string; cited_text: string | null }[];
    confidence: number;
    summary: string;
  };
  compensation_signal: string;
  policy_version: string;
  prompt_version: string;
  model_route: string;
  usage: {
    model_id: string;
    input_tokens: number;
    output_tokens: number;
    latency_ms: number;
    repair_attempts: number;
  };
}

export interface CaseDetail {
  case_id: string;
  state: string;
  url: string;
  created_at: string;
  updated_at: string;
  snapshot: {
    snapshot_id: string;
    final_url: string;
    authority_level: string;
    http_status: number;
    content_hash: string;
    retrieved_at: string;
  } | null;
  verification: {
    status: string;
    authority: string;
    checked_at: string;
    reason: string;
    content_changed: boolean | null;
  } | null;
  evaluation: EvaluationView | null;
  gated_decision: string | null;
  approval: ApprovalRequestView | null;
}

export async function listCases(): Promise<CaseSummary[]> {
  const res = await fetch(`${API_BASE}/v1/cases`, { cache: "no-store" });
  if (!res.ok) throw new Error(`list failed: ${res.status}`);
  return res.json();
}

export async function getCase(caseId: string): Promise<CaseDetail> {
  const res = await fetch(`${API_BASE}/v1/cases/${caseId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`detail failed: ${res.status}`);
  return res.json();
}

export async function createCase(
  url: string,
  idempotencyKey: string,
): Promise<{ case_id: string }> {
  // The caller owns the key for the lifetime of one submission intent, so a
  // retried or re-clicked submission replays instead of duplicating the case.
  const res = await fetch(`${API_BASE}/v1/cases`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(`create failed: ${res.status}`);
  return res.json();
}

// The reviewer's own observation that the posting is open (ADR-013). It is
// sent only with APPROVE on a posting the system could not verify; the
// server re-checks the gate with it and audits it separately.
export interface Attestation {
  observed_open: true;
  note: string;
}

export async function submitDecision(
  approvalRequestId: string,
  decision: string,
  reason: string,
  attestation?: Attestation,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/v1/approvals/${approvalRequestId}/decisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        reason,
        decision_version: 1,
        actor: "admin",
        ...(attestation ? { attestation } : {}),
      }),
    },
  );
  if (!res.ok) throw new Error(`decision failed: ${res.status}`);
}
