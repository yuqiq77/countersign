"""Budget units of the scheduled watch (ADR-016), in model calls."""

# One recheck at worst: three reader steps, each with one schema repair.
RECHECK_MODEL_CALLS = 6
# One new case at worst: its reading, then an evaluation with one repair.
NEW_CASE_MODEL_CALLS = 8
