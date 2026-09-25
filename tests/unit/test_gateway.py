"""Gateway behavior: determinism, repair budget, deterministic signal."""

import pytest

from countersign.contracts.enums import (
    CompensationSignal,
    EvalDecision,
    JobStatus,
    SeniorityFit,
    SourceAuthority,
)
from countersign.contracts.evaluation import ModelEvaluation, SalaryExtraction
from countersign.domain.compensation import SalaryBands
from countersign.gateway.evaluator import EvaluatorConfig, run_evaluation
from countersign.gateway.fake_provider import FakeEvaluationProvider
from countersign.gateway.provider import ContractFailure

BANDS = SalaryBands(currency="USD", acceptable_min=90_000, attractive_min=120_000)


def run(
    provider: FakeEvaluationProvider,
    *,
    config: EvaluatorConfig | None = None,
    bands: SalaryBands = BANDS,
    profile: str | None = None,
):
    return run_evaluation(
        provider,
        case_id="case-1",
        content_hash="abc123def4567890",
        url="https://example.com/jobs/1",
        title="Engineer",
        text="A job posting body.",
        verification=None,
        bands=bands,
        profile=profile,
        config=config,
    )


def test_fake_provider_is_deterministic() -> None:
    first = run(FakeEvaluationProvider())
    second = run(FakeEvaluationProvider())
    assert first.evaluation == second.evaluation
    assert first.evaluation_id == second.evaluation_id
    assert first.evaluation_id.startswith("eval-case-1-")


def test_evaluation_id_spans_every_version_dimension() -> None:
    """Same content under a different policy/prompt/route/generation is a
    different evaluation id — never a silent no-op on record."""

    base = run(FakeEvaluationProvider())
    other_policy = run(
        FakeEvaluationProvider(),
        config=EvaluatorConfig(policy_version="default-rubric/9.9.9"),
    )
    assert base.evaluation_id != other_policy.evaluation_id

    other_generation = run_evaluation(
        FakeEvaluationProvider(),
        case_id="case-1",
        content_hash="abc123def4567890",
        url="https://example.com/jobs/1",
        title="Engineer",
        text="A job posting body.",
        verification=None,
        bands=BANDS,
        generation=2,
    )
    assert base.evaluation_id != other_generation.evaluation_id


def test_repair_retry_recovers_one_failure() -> None:
    provider = FakeEvaluationProvider(fail_attempts=1)
    record = run(provider)
    assert provider.calls == 2
    assert record.usage.repair_attempts == 1


def test_repair_budget_exhaustion_raises_contract_failure() -> None:
    provider = FakeEvaluationProvider(fail_attempts=2)
    with pytest.raises(ContractFailure):
        run(provider, config=EvaluatorConfig(max_repair_attempts=1))


def test_compensation_signal_is_computed_not_modeled() -> None:
    canned = ModelEvaluation(
        decision=EvalDecision.STRETCH,
        application_status=JobStatus.OPEN,
        skill_capital=8,
        strategic_alignment=7,
        learning_velocity=8,
        drift_risk=3,
        domain_lock_in_risk=2,
        transition_feasibility=5,
        seniority_fit=SeniorityFit.PLAUSIBLE_STRETCH,
        salary_extraction=SalaryExtraction(
            official=True,
            currency="USD",
            min_amount=None,
            max_amount=130_000,
            source_authority=SourceAuthority.OFFICIAL_COMPANY,
            cited_text="up to $130,000",
        ),
        confidence=0.8,
        summary="canned",
    )
    record = run(FakeEvaluationProvider(canned=canned))
    assert record.compensation_signal == CompensationSignal.ATTRACTIVE

    mismatched = canned.model_copy(
        update={
            "salary_extraction": canned.salary_extraction.model_copy(  # type: ignore[union-attr]
                update={"currency": "EUR"}
            )
        }
    )
    record = run(FakeEvaluationProvider(canned=mismatched))
    assert record.compensation_signal == CompensationSignal.UNKNOWN


def test_versions_are_stamped() -> None:
    record = run(FakeEvaluationProvider())
    assert record.policy_version == f"default-rubric/0.2.0+bands.{BANDS.fingerprint()}+profile.none"
    assert record.prompt_version.startswith("evaluator/")
    assert record.model_route == "reasoning-standard"


def test_different_salary_bands_are_different_evaluations() -> None:
    """Bands feed the compensation signal, so they are policy identity:
    same posting, other bands → other policy_version and evaluation id."""

    other = SalaryBands(currency="EUR", acceptable_min=70_000, attractive_min=95_000)
    same = SalaryBands(currency="USD", acceptable_min=90_000, attractive_min=120_000)
    base = run(FakeEvaluationProvider())
    assert run(FakeEvaluationProvider(), bands=same).evaluation_id == base.evaluation_id
    moved = run(FakeEvaluationProvider(), bands=other)
    assert moved.evaluation_id != base.evaluation_id
    assert moved.policy_version != base.policy_version
    assert len(moved.policy_version) <= 64  # persisted column width


def test_operator_profile_is_policy_identity_too() -> None:
    base = run(FakeEvaluationProvider())
    with_profile = run(FakeEvaluationProvider(), profile="Engineer, fluent in two languages.")
    edited = run(FakeEvaluationProvider(), profile="Engineer, fluent in three languages.")
    assert base.policy_version.endswith("+profile.none")
    assert with_profile.evaluation_id != base.evaluation_id
    assert edited.evaluation_id != with_profile.evaluation_id
    assert (
        run(FakeEvaluationProvider(), profile="Engineer, fluent in two languages.").evaluation_id
        == with_profile.evaluation_id
    )
    assert len(with_profile.policy_version) <= 64


def test_salary_source_facts_are_stamped_from_verification() -> None:
    """The model extracts amounts; where they came from is decided by code.

    Two live runs on one posting labelled the same salary OFFICIAL_ATS and
    OFFICIAL_COMPANY. The verification's authority wins, and `official`
    follows the authority tier rather than the model's opinion.
    """

    from datetime import UTC, datetime

    from countersign.contracts.verification import VerificationResult

    def verification(authority: SourceAuthority) -> VerificationResult:
        return VerificationResult(
            case_id="case-1",
            status=JobStatus.OPEN,
            authority=authority,
            checked_at=datetime(2026, 9, 19, tzinfo=UTC),
            http_status=200,
            final_url="https://example.com/jobs/1",
            reason="test",
            apply_markers_found=True,
            closed_markers_found=False,
        )

    canned = ModelEvaluation(
        decision=EvalDecision.STRETCH,
        application_status=JobStatus.OPEN,
        skill_capital=8,
        strategic_alignment=7,
        learning_velocity=8,
        drift_risk=3,
        domain_lock_in_risk=2,
        transition_feasibility=5,
        seniority_fit=SeniorityFit.PLAUSIBLE_STRETCH,
        salary_extraction=SalaryExtraction(
            official=True,
            currency="USD",
            min_amount=50_000,
            max_amount=80_000,
            source_authority=SourceAuthority.OFFICIAL_COMPANY,
            cited_text="50-80K",
        ),
        confidence=0.8,
        summary="canned",
    )

    def run_with(
        authority: SourceAuthority, body_authority: SourceAuthority | None = None
    ) -> SalaryExtraction:
        record = run_evaluation(
            FakeEvaluationProvider(canned=canned),
            case_id="case-1",
            content_hash="abc123def4567890",
            url="https://example.com/jobs/1",
            title="Engineer",
            text="A job posting body.",
            verification=verification(authority),
            source_authority=body_authority,
            bands=BANDS,
        )
        assert record.evaluation.salary_extraction is not None
        return record.evaluation.salary_extraction

    ats = run_with(SourceAuthority.OFFICIAL_ATS)
    assert ats.source_authority == SourceAuthority.OFFICIAL_ATS
    assert ats.official is True
    assert (ats.min_amount, ats.max_amount) == (50_000, 80_000)

    company_page = run_with(SourceAuthority.COMPANY_CONTROLLED)
    assert company_page.source_authority == SourceAuthority.COMPANY_CONTROLLED
    assert company_page.official is False

    board_body = run_with(SourceAuthority.OFFICIAL_ATS, SourceAuthority.COMPANY_CONTROLLED)
    assert board_body.source_authority == SourceAuthority.COMPANY_CONTROLLED
    assert board_body.official is False


def test_transport_failure_is_not_repaired_or_quarantined(monkeypatch: pytest.MonkeyPatch) -> None:
    from countersign.gateway.provider import ProviderResponse, ProviderUnavailable

    provider = FakeEvaluationProvider()
    calls: list[str] = []

    def unavailable(*, system: str, user: str) -> ProviderResponse:
        calls.append(user)
        raise ProviderUnavailable("offline")

    monkeypatch.setattr(provider, "evaluate", unavailable)
    with pytest.raises(ProviderUnavailable):
        run(provider)
    assert len(calls) == 1


def test_operator_context_rides_in_the_user_prompt_and_is_cited_by_the_record() -> None:
    """ADR-014: reviewer notes are labelled human-asserted, appear after the
    posting text and never in the cached system prompt, and the record names
    the decisions it consumed."""

    from datetime import UTC, datetime

    from countersign.contracts.cases import OperatorContext
    from countersign.gateway.evaluator import build_system_prompt, build_user_prompt

    notes = [
        OperatorContext(
            decision_id="dec-appr-case-1-g1-v1",
            supplied_at=datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
            text="Completed a structured interview-preparation course this year.",
        )
    ]
    user = build_user_prompt(
        url="https://example.com/jobs/1",
        title="Engineer",
        text="A job posting body.",
        verification=None,
        operator_context=notes,
    )
    posting_end = user.index("--- END POSTING TEXT ---")
    block = user.index(
        "--- OPERATOR-SUPPLIED CONTEXT (human-asserted, not observed on the posting) ---"
    )
    assert posting_end < block
    assert "[2026-09-25] Completed a structured interview-preparation course" in user
    assert user.endswith("--- END OPERATOR-SUPPLIED CONTEXT ---")
    assert "OPERATOR-SUPPLIED CONTEXT" not in build_system_prompt()
    assert "Treat case context as human assertions" in build_system_prompt()

    without = build_user_prompt(
        url="https://example.com/jobs/1",
        title="Engineer",
        text="A job posting body.",
        verification=None,
    )
    assert "OPERATOR-SUPPLIED CONTEXT" not in without

    record = run_evaluation(
        FakeEvaluationProvider(),
        case_id="case-1",
        content_hash="abc123def4567890",
        url="https://example.com/jobs/1",
        title="Engineer",
        text="A job posting body.",
        verification=None,
        bands=BANDS,
        operator_context=notes,
        generation=2,
    )
    assert record.operator_context_ids == ["dec-appr-case-1-g1-v1"]
    assert record.prompt_version == "evaluator/0.3.0"
    assert run(FakeEvaluationProvider()).operator_context_ids == []
