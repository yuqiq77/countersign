"""Deterministic fake provider for tests and model-free local runs."""

import hashlib
from typing import cast

from countersign.contracts.base import ContractModel
from countersign.contracts.discovery import (
    ScreenVerdict,
    SearchBrief,
    SearchCandidates,
)
from countersign.contracts.enums import (
    ApplyEntryKind,
    EvalDecision,
    JobStatus,
    ReaderAction,
    ScreenDecision,
    SeniorityFit,
)
from countersign.contracts.evaluation import ModelEvaluation
from countersign.contracts.page_reading import ApplyEntry, PageReading, PageStep
from countersign.gateway.provider import (
    ContractFailure,
    ParsedResponse,
    ProviderResponse,
    SearchResponse,
)

FAKE_BRIEF = SearchBrief(
    role_families=["software engineer"],
    skills=["python"],
    regions=["example city"],
    languages=["english"],
    exclude_roles=["sales"],
)


def fake_search_response(
    candidates: SearchCandidates | None = None,
    *,
    results: list[tuple[str, str]] | None = None,
    searches: int = 0,
    stop_reason: str = "end_turn",
) -> SearchResponse:
    """A search request's response as the fake backend gives it."""

    found = results or []
    return SearchResponse(
        parsed=None
        if stop_reason == "pause_turn"
        else (candidates or SearchCandidates(candidates=[])),
        stop_reason=stop_reason,
        queries=sorted({query for query, _ in found}),
        results=found,
        searches=searches,
        model_id="fake-model",
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _score(seed: bytes, salt: str) -> int:
    digest = hashlib.sha256(seed + salt.encode()).digest()
    return digest[0] % 10 + 1


class FakeEvaluationProvider:
    """Derives a stable, contract-valid evaluation from the input text.

    `fail_attempts` makes the first N calls raise ContractFailure so the
    gateway's repair path is testable without a live model. `page_steps`
    scripts the page reader: each call for a `PageStep` pops the next one;
    with none left the fake answers DONE with an empty reading (unassessed).
    Discovery (ADR-017): `brief` answers the brief, `verdicts` the screens in
    order (PASS when none is left), and `searches` the search requests in
    order, each a response or an exception to raise (no search and no
    candidate when none is left). `search_uses` records each request's
    `max_uses`.
    """

    def __init__(
        self,
        *,
        fail_attempts: int = 0,
        canned: ModelEvaluation | None = None,
        page_steps: list[PageStep] | None = None,
        brief: SearchBrief | None = None,
        verdicts: list[ScreenVerdict] | None = None,
        searches: list[SearchResponse | Exception] | None = None,
    ) -> None:
        self._remaining_failures = fail_attempts
        self._canned = canned
        self._page_steps = list(page_steps or [])
        self._brief = brief
        self._verdicts = list(verdicts or [])
        self._searches = list(searches or [])
        self.calls = 0
        self.search_uses: list[int] = []

    def parse[T: ContractModel](
        self, *, system: str, user: str, output_type: type[T], timeout_seconds: float | None = None
    ) -> ParsedResponse[T]:
        self.calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ContractFailure("fake provider: induced contract failure")
        parsed: ContractModel
        if output_type is ModelEvaluation:
            parsed = self._canned if self._canned is not None else self._derive(user)
        elif output_type is PageStep:
            parsed = (
                self._page_steps.pop(0)
                if self._page_steps
                else PageStep(
                    action=ReaderAction.DONE,
                    why="fake: no scripted assessment",
                    reading=PageReading(
                        body_observation=0, apply_entry=ApplyEntry(kind=ApplyEntryKind.NONE)
                    ),
                )
            )
        elif output_type is SearchBrief:
            parsed = self._brief if self._brief is not None else FAKE_BRIEF
        elif output_type is ScreenVerdict:
            parsed = (
                self._verdicts.pop(0)
                if self._verdicts
                else ScreenVerdict(verdict=ScreenDecision.PASS, reason="fake: no scripted verdict")
            )
        else:
            raise ContractFailure(f"fake provider cannot produce {output_type.__name__}")
        return ParsedResponse(
            parsed=cast(T, parsed),
            model_id="fake-model",
            input_tokens=len(system) // 4 + len(user) // 4,
            output_tokens=256,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )

    def search(
        self, *, system: str, messages: list[dict[str, object]], max_uses: int
    ) -> SearchResponse:
        self.calls += 1
        self.search_uses.append(max_uses)
        if self._searches:
            scripted = self._searches.pop(0)
            if isinstance(scripted, Exception):
                raise scripted
            return scripted
        return fake_search_response()

    def evaluate(self, *, system: str, user: str) -> ProviderResponse:
        response = self.parse(system=system, user=user, output_type=ModelEvaluation)
        return ProviderResponse(
            evaluation=response.parsed,
            model_id=response.model_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_input_tokens=response.cache_read_input_tokens,
            cache_creation_input_tokens=response.cache_creation_input_tokens,
        )

    def _derive(self, user: str) -> ModelEvaluation:
        seed = hashlib.sha256(user.encode()).digest()
        return ModelEvaluation(
            decision=EvalDecision.STRETCH,
            application_status=JobStatus.UNVERIFIED,
            skill_capital=_score(seed, "skill"),
            strategic_alignment=_score(seed, "alignment"),
            learning_velocity=_score(seed, "velocity"),
            drift_risk=_score(seed, "drift"),
            domain_lock_in_risk=_score(seed, "lockin"),
            transition_feasibility=_score(seed, "feasibility"),
            seniority_fit=SeniorityFit.PLAUSIBLE_STRETCH,
            salary_extraction=None,
            hard_blockers=[],
            missing_evidence=["deterministic fake evaluation"],
            distinctive_assets=[],
            risk_flags=[],
            confidence=0.5,
            summary="Deterministic fake evaluation derived from input hash.",
        )
