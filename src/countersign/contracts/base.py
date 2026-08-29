"""Strict defaults for data crossing application trust boundaries."""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base model for immutable, strict, whitespace-normalized contracts.

    `strict=True` rejects lax coercions (e.g. the string "1" for an int
    field) in Python-mode validation; JSON-mode validation (Temporal's
    pydantic converter, `model_validate_json`) still accepts JSON-native
    representations such as ISO datetime strings and enum values.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class JsonBoundaryModel(BaseModel):
    """Base for models FastAPI validates from parsed JSON (Python mode).

    FastAPI parses the request body with `json.loads` and validates the
    resulting Python objects, where strict mode would reject enum values
    arriving as plain strings. These models therefore stay lax at the model
    level; numeric fields opt back into strictness with `Field(strict=True)`
    so numbers-as-strings are still rejected.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )
