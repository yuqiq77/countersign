"""Shared round identities for persistence and approval Signal matching (ADR-014)."""


def case_workflow_id(case_id: str) -> str:
    """One stable workflow id per case (v4 §20); rounds share it (ADR-014)."""

    return f"job-case/{case_id}"


def approval_request_id(case_id: str, generation: int) -> str:
    return f"appr-{case_id}-g{generation}"


def verification_id(case_id: str, generation: int) -> str:
    return f"ver-{case_id}-g{generation}"


def reading_id(case_id: str, generation: int) -> str:
    return f"read-{case_id}-g{generation}"


def recheck_verification_id(case_id: str, generation: int, run_key: str) -> str:
    """A watch run's recheck of one round (ADR-016); the round's own id is untouched."""

    return f"{verification_id(case_id, generation)}-r{run_key}"


def recheck_reading_id(case_id: str, generation: int, run_key: str) -> str:
    return f"{reading_id(case_id, generation)}-r{run_key}"
