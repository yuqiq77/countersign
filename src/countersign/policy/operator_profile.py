"""The operator's evaluator profile: private policy text, loaded by path.

`COUNTERSIGN_OPERATOR_PROFILE_PATH` names the file; Compose mounts
`config/operator/` read-only and points at it. Unset means the profile is
not provided — a fact the prompt then states verbatim, so the model never
has to judge whether a profile is present. There is deliberately no
implicit default path: a test run or a host process never picks up an
operator's file by accident of the working directory.
"""

from pathlib import Path

from countersign.env import optional_env

PROFILE_PATH_VAR = "COUNTERSIGN_OPERATOR_PROFILE_PATH"
MAX_PROFILE_CHARS = 40_000


def load_operator_profile() -> str | None:
    """The profile text, or None when the path variable is unset.

    A set path must name a non-empty, readable file within the size limit;
    anything else raises ValueError (the worker refuses to start on it)
    rather than silently evaluating without the profile.
    """

    path = optional_env(PROFILE_PATH_VAR)
    if path is None:
        return None
    file = Path(path)
    if not file.is_file():
        raise ValueError(f"{PROFILE_PATH_VAR} points at {path!r}, which is not a file")
    text = file.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"operator profile at {path!r} is empty")
    if len(text) > MAX_PROFILE_CHARS:
        raise ValueError(
            f"operator profile at {path!r} is {len(text)} characters; "
            f"the limit is {MAX_PROFILE_CHARS}"
        )
    return text
