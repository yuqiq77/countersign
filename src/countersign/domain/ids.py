"""Shared round identities for persistence and approval Signal matching (ADR-014)."""


def approval_request_id(case_id: str, generation: int) -> str:
    return f"appr-{case_id}-g{generation}"


def verification_id(case_id: str, generation: int) -> str:
    return f"ver-{case_id}-g{generation}"


def reading_id(case_id: str, generation: int) -> str:
    return f"read-{case_id}-g{generation}"
