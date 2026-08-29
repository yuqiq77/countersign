"""Operational API contracts."""

from typing import Literal

from countersign.contracts.base import ContractModel


class HealthResponse(ContractModel):
    """Liveness response; dependency readiness will be added in Slice 1."""

    status: Literal["ok"] = "ok"
    service: Literal["countersign-api"] = "countersign-api"
