"""Budget units of the scheduled watch (ADR-016, ADR-017), in model calls."""

# One recheck at worst: three reader steps, each with one schema repair.
RECHECK_MODEL_CALLS = 6
# One new case at worst: its reading, then an evaluation with one repair.
NEW_CASE_MODEL_CALLS = 8
# The search brief at worst: one call and one schema repair.
BRIEF_MODEL_CALLS = 2
# One screen at worst: one call and one schema repair.
SCREEN_MODEL_CALLS = 2
# The search step at worst: its first request and two continuations.
SEARCH_MAX_REQUESTS = 3
