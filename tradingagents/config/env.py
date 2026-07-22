"""Typed environment-variable accessors — the config boundary.

Before this module the codebase had the same coercion idiom copy-pasted ~50
times, in two *incompatible* dialects:

  * permissive:  ``os.getenv(X, "false").strip().lower() in ("1","true","yes","on")``
  * strict:      ``os.getenv(X, "false").strip().lower() == "true"``

``env_bool`` standardizes on the **permissive** dialect, which is what the
majority of feature flags already used. It is a byte-for-byte behavioral match
for the permissive call sites (verified in tests), so migrating them changes
nothing. The strict ``== "true"`` sites are intentionally NOT auto-migrated:
``env_bool`` would make ``FLAG=yes`` newly truthy there, which is a behavior
change. Migrate those deliberately, one at a time, if/when desired.

Design: pure, stdlib-only, no caching. Env is read fresh each call so an
operator can flip a flag and a long-running loop picks it up on its next tick —
matching the existing runtime-reconfigurable behavior.
"""
from __future__ import annotations

import os

# The permissive truthy set the majority of flags already used. Kept as a
# frozenset so membership is O(1) and the set is immutable.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def env_bool(name: str, default: bool = False) -> bool:
    """Parse an env var as a boolean using the permissive truthy set.

    Returns ``default`` when the var is unset. A set-but-unrecognized value
    (e.g. "false", "", "0", "no") is falsey. Exactly equivalent to the legacy
    ``os.getenv(name, "true"/"false").strip().lower() in ("1","true","yes","on")``
    idiom, so it is a no-op replacement for those call sites.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def env_int(name: str, default: int) -> int:
    """Parse an env var as an int; fall back to ``default`` on unset/garbage.

    Never raises — a malformed value degrades to the default rather than
    crashing a background loop mid-tick (the legacy code wrapped these in
    try/except with the same intent, ad-hoc, everywhere)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(float(raw.strip()))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    """Parse an env var as a float; fall back to ``default`` on unset/garbage."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return default


def env_str(name: str, default: str = "") -> str:
    """Read an env var as a stripped string; ``default`` when unset."""
    raw = os.getenv(name)
    return default if raw is None else raw.strip()


def env_list(name: str, default: list[str] | None = None, *, sep: str = ",") -> list[str]:
    """Parse a delimited env var into a list of trimmed, non-empty tokens.

    ``FOO=a, b ,,c`` → ``["a", "b", "c"]``. Unset → ``default`` (or ``[]``)."""
    raw = os.getenv(name)
    if raw is None:
        return list(default) if default is not None else []
    return [tok.strip() for tok in raw.split(sep) if tok.strip()]
