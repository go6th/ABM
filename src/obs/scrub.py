"""The allow-list and shape filter applied at every sink (pure; O3, O4).

``clean(mapping, allowed, cap)`` keeps only allowed keys whose values pass the shape checks, and
returns the number of dropped entries (counted as ``telemetry_dropped_total``). A value is
refused when it is a string that is too long, holds a URL with a query, fragment or credentials, an
email address, a token or key shape, control characters, or a planted canary. ``scrub_exception``
keeps the class name, a typed code and a hash of the message, never the message, locals or body.

GUARANTEE(test_clean_never_passes_unknown_keys_or_bad_shapes): property test (O3).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

URL_WITH_EXTRA = re.compile(r"[a-z][a-z0-9+.-]*://[^\s]*([?#@]|:[^/\s]*@)", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKENISH = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16}|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}|[A-Za-z0-9+/]{40,}={0,2})",
    re.I,
)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
CANARY = re.compile(r"CANARY-[0-9a-z]{4,}", re.I)
SCALAR = (bool, int, float)


def value_ok(value: Any, cap: int, canaries: Iterable[str] = ()) -> bool:
    """Whether one value may reach a sink."""
    if value is None or isinstance(value, SCALAR):
        return True
    if not isinstance(value, str):
        return False
    if len(value) > cap or CONTROL.search(value):
        return False
    if URL_WITH_EXTRA.search(value) or EMAIL.search(value) or TOKENISH.search(value):
        return False
    if CANARY.search(value):
        return False
    lowered = value.casefold()
    return not any(c and c.casefold() in lowered for c in canaries)


def clean(
    attributes: Mapping[str, Any],
    allowed: frozenset[str],
    cap: int,
    canaries: Iterable[str] = (),
) -> tuple[dict[str, Any], int]:
    """The allowed, well-shaped entries and how many were dropped."""
    kept: dict[str, Any] = {}
    dropped = 0
    canary_list = tuple(canaries)
    for key, value in attributes.items():
        if key in allowed and value_ok(value, cap, canary_list):
            kept[key] = value
        else:
            dropped += 1
    return kept, dropped


def message_hash(text: str) -> str:
    """A short hash standing in for a message that must not be stored."""
    return hashlib.sha256(text.encode(errors="replace")).hexdigest()[:16]


# the first path segments of this product's own packages (installed under site-packages)
_OWN_PACKAGES = frozenset(
    {
        "account_intelligence", "agents", "documents", "exports", "llm", "obs", "identity",
        "metering", "jobs", "people", "spend", "views", "signals", "governance", "parallel",
        "tracker", "grading", "connectors", "verify", "replica", "workflow",
    }
)  # fmt: skip


def _package_path(filename: str) -> str:
    """The module path under the package root: the part after ``site-packages/`` (the image)
    or after ``src/`` (a checkout), with the separators made ``/``."""
    name = filename.replace("\\", "/")
    for marker in ("site-packages/", "/src/"):
        if marker in name:
            return name.rsplit(marker, 1)[-1]
    return name.rsplit("/", 1)[-1]


def error_where(exc: BaseException) -> str | None:
    """The innermost frame of this product's own code that raised: ``package/module.py:line:
    function``. A code location, never the message, so an error whose text must not be logged
    still says where it came from (2026-10-22: a RuntimeError with only a hash could not be
    traced)."""
    import traceback

    frames = traceback.extract_tb(exc.__traceback__)
    own = [f for f in frames if _package_path(f.filename).split("/", 1)[0] in _OWN_PACKAGES]
    if own:
        frame = own[-1]
        return f"{_package_path(frame.filename)}:{frame.lineno}:{frame.name}"[:200]
    if not frames:
        return None
    # no own frame (a library's background task, an async generator finaliser): the innermost
    # library frame, marked ``ext:`` (staging 2026-10-24: four RuntimeErrors with only a hash)
    frame = frames[-1]
    return f"ext:{_package_path(frame.filename)}:{frame.lineno}:{frame.name}"[:200]


def scrub_exception(exc: BaseException) -> dict[str, str]:
    """Class, typed code, message hash and the own-code location only (O3)."""
    code = getattr(exc, "code", None) or getattr(exc, "cap_reason", None) or type(exc).__name__
    out = {
        "error_class": type(exc).__name__,
        "error_code": str(code)[:64] if value_ok(str(code), 64) else type(exc).__name__,
        "message_hash": message_hash(str(exc)),
    }
    where = error_where(exc)
    if where:
        out["where"] = where
    return out
