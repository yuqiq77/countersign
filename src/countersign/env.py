"""Environment reading with one rule: blank means unset.

Compose passes `${VAR:-}` through as an empty string, and both vendor SDKs
and `int()` treat an empty string as a value. Every optional setting in
the process is read through here so the rule is applied once.
"""

import os


def optional_env(name: str) -> str | None:
    """Read an environment variable, treating blank as unset."""

    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def clear_blank_env(name: str) -> None:
    """Remove `name` from the process environment when its value is blank.

    For a setting a vendor SDK resolves itself (environment, then its own
    profile files, then a default), a blank value would count as explicit
    and short-circuit that chain. Clearing it lets the SDK run the chain.
    """

    if name in os.environ and not os.environ[name].strip():
        del os.environ[name]
