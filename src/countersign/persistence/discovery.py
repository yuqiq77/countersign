"""Repository functions of profile-driven discovery (ADR-017, ticket 005)."""

import hashlib
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from countersign.contracts.discovery import BriefView, CandidateView, SearchBrief
from countersign.contracts.enums import CandidateSource, CandidateStatus, ScreenDecision
from countersign.contracts.evaluation import ModelUsage
from countersign.domain.discovery import VerdictInputs
from countersign.domain.watch import BRIEF_MODEL_CALLS
from countersign.persistence.models import (
    DiscoveryCandidateRow,
    DiscoverySearchRow,
    SearchBriefRow,
)
from countersign.persistence.repo import ensure_utc, utcnow


def candidate_id(posting_key: str) -> str:
    return hashlib.sha256(posting_key.encode("utf-8")).hexdigest()[:32]


def get_candidate(session: Session, posting_key: str) -> DiscoveryCandidateRow | None:
    return session.get(DiscoveryCandidateRow, candidate_id(posting_key))


def record_sighting(
    session: Session,
    *,
    posting_key: str,
    url: str,
    source: CandidateSource,
    origin: str,
    title: str,
    company: str | None,
    locations: list[str],
    seen_at: datetime,
    status: CandidateStatus = CandidateStatus.UNSCREENED,
    department: str | None = None,
    team: str | None = None,
    excerpt: str | None = None,
    published_at: datetime | None = None,
    note: str | None = None,
) -> tuple[DiscoveryCandidateRow, bool]:
    """Record that a source returned a posting; True when it is new.

    A posting both sources return stays with the source that recorded it
    first. A board refreshes its posting's listing facts, so a changed title
    or publication time makes the verdict stale; a search result keeps the
    facts it was first recorded with, since its title is the model's
    transcription and would vary from run to run. `note` fills the reason
    of a new row (the search's own reason) until a screen replaces it.
    """

    row = get_candidate(session, posting_key)
    if row is None:
        last = session.scalar(select(func.max(DiscoveryCandidateRow.seq)))
        row = DiscoveryCandidateRow(
            id=candidate_id(posting_key),
            posting_key=posting_key,
            url=url,
            source=source.value,
            origin=origin[:512],
            title=title[:512],
            company=company[:256] if company else None,
            locations=locations,
            department=department,
            team=team,
            excerpt=excerpt,
            published_at=published_at,
            first_seen_at=seen_at,
            seq=(last or 0) + 1,
            last_seen_at=seen_at,
            status=status.value,
            reason=note[:500] if note else None,
            failed_open=False,
        )
        session.add(row)
        return row, True
    if ensure_utc(row.last_seen_at) < seen_at:
        row.last_seen_at = seen_at
    if row.source == source.value == CandidateSource.BOARD.value:
        row.url = url
        row.title = title[:512]
        row.locations = locations
        row.department = department
        row.team = team
        row.excerpt = excerpt
        row.published_at = published_at
    return row, False


def open_candidates(session: Session, drops_since: datetime) -> list[DiscoveryCandidateRow]:
    """Candidates that may still be screened or admitted: unscreened and passed
    ones, and drops seen since `drops_since` (an older drop comes back only
    when a source returns it again)."""

    stmt = select(DiscoveryCandidateRow).where(
        or_(
            DiscoveryCandidateRow.status.in_(
                [CandidateStatus.UNSCREENED.value, CandidateStatus.PASS.value]
            ),
            and_(
                DiscoveryCandidateRow.status == CandidateStatus.DROP.value,
                DiscoveryCandidateRow.last_seen_at >= drops_since,
            ),
        )
    )
    return list(session.scalars(stmt))


def verdict_inputs(row: DiscoveryCandidateRow) -> VerdictInputs | None:
    if (
        row.verdict_profile is None
        or row.verdict_prompt_version is None
        or row.verdict_route is None
        or row.verdict_listing_hash is None
    ):
        return None
    return VerdictInputs(
        row.verdict_profile, row.verdict_prompt_version, row.verdict_route, row.verdict_listing_hash
    )


def record_verdict(
    session: Session,
    row: DiscoveryCandidateRow,
    *,
    decision: ScreenDecision,
    reason: str,
    failed_open: bool,
    inputs: VerdictInputs,
    usage: dict[str, object] | None,
    at: datetime,
) -> None:
    """Store a screen verdict. A drop that passes later joins the queue's back."""

    passed_before = row.status == CandidateStatus.PASS.value and row.first_passed_at is not None
    row.status = decision.value
    row.reason = reason
    row.failed_open = failed_open
    (
        row.verdict_profile,
        row.verdict_prompt_version,
        row.verdict_route,
        row.verdict_listing_hash,
    ) = inputs
    row.screened_at = at
    row.usage = usage
    if decision == ScreenDecision.PASS and not passed_before:
        row.first_passed_at = at


def mark_admitted(session: Session, posting_key: str, case_id: str) -> None:
    row = get_candidate(session, posting_key)
    if row is not None:
        row.status = CandidateStatus.ADMITTED.value
        row.case_id = case_id


def list_candidates(
    session: Session, status: CandidateStatus, since: datetime
) -> list[CandidateView]:
    """Candidates in one status, last seen since `since`, newest first."""

    stmt = (
        select(DiscoveryCandidateRow)
        .where(
            DiscoveryCandidateRow.status == status.value,
            DiscoveryCandidateRow.last_seen_at >= since,
        )
        .order_by(DiscoveryCandidateRow.last_seen_at.desc())
    )
    return [
        CandidateView(
            id=row.id,
            key=row.posting_key,
            url=row.url,
            source=CandidateSource(row.source),
            origin=row.origin,
            title=row.title,
            company=row.company,
            status=CandidateStatus(row.status),
            reason=row.reason,
            first_seen_at=ensure_utc(row.first_seen_at),
            last_seen_at=ensure_utc(row.last_seen_at),
        )
        for row in session.scalars(stmt)
    ]


def brief_id(profile_fingerprint: str, prompt_version: str) -> str:
    return f"{profile_fingerprint}:{prompt_version}"


def get_brief(
    session: Session, profile_fingerprint: str, prompt_version: str
) -> SearchBrief | None:
    row = session.get(SearchBriefRow, brief_id(profile_fingerprint, prompt_version))
    return None if row is None else SearchBrief.model_validate(row.brief)


def stored_brief(
    session: Session, profile_fingerprint: str, prompt_version: str
) -> tuple[SearchBrief, int] | None:
    """The stored brief and the model calls its derivation made (repairs plus one)."""

    row = session.get(SearchBriefRow, brief_id(profile_fingerprint, prompt_version))
    if row is None:
        return None
    usage = ModelUsage.model_validate(row.usage) if row.usage is not None else None
    calls = usage.repair_attempts + 1 if usage is not None else BRIEF_MODEL_CALLS
    return SearchBrief.model_validate(row.brief), calls


def save_brief(
    session: Session,
    *,
    profile_fingerprint: str,
    prompt_version: str,
    route: str,
    brief: SearchBrief,
    usage: dict[str, object] | None,
) -> SearchBrief:
    """Store the brief for a profile and prompt and return the stored one: a
    derivation that finds a brief already stored keeps and returns the first."""

    key = brief_id(profile_fingerprint, prompt_version)
    existing = session.get(SearchBriefRow, key)
    if existing is not None:
        return SearchBrief.model_validate(existing.brief)
    session.add(
        SearchBriefRow(
            id=key,
            profile_fingerprint=profile_fingerprint,
            brief=brief.model_dump(mode="json"),
            prompt_version=prompt_version,
            model_route=route,
            usage=usage,
            created_at=utcnow(),
        )
    )
    return brief


def latest_brief(session: Session) -> BriefView | None:
    stmt = select(SearchBriefRow).order_by(SearchBriefRow.created_at.desc())
    row = session.scalars(stmt).first()
    if row is None:
        return None
    return BriefView(
        profile_fingerprint=row.profile_fingerprint,
        brief=SearchBrief.model_validate(row.brief),
        prompt_version=row.prompt_version,
        model_route=row.model_route,
        created_at=ensure_utc(row.created_at),
    )


def record_search_request(
    session: Session,
    *,
    run_key: str,
    attempt: int,
    index: int,
    queries: list[str],
    searches: int,
    usage: dict[str, object],
) -> None:
    """Store what one search request searched for.

    Keyed by the Activity attempt as well: a retried search step searches
    again, and those searches left the system too.
    """

    key = f"{run_key}-{attempt}-{index}"
    if session.get(DiscoverySearchRow, key) is not None:
        return
    session.add(
        DiscoverySearchRow(
            id=key,
            run_key=run_key,
            request_index=index,
            queries=queries,
            searches=searches,
            usage=usage,
            created_at=utcnow(),
        )
    )
