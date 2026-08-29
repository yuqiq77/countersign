"""Strict defaults for data crossing application trust boundaries."""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base model for immutable, strict, whitespace-normalized contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )
