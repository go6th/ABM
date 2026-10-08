"""The suppression list (PD4): an erased person stays erased.

Salted hashes of the profile URL and the normalised name are stored (never the
values themselves). Pipelines check the list before storing a person and
exports check it before rendering; restoring from a backup re-applies pending
erasures and suppressions before the system serves traffic (the restore drill).
Pure hashing here; the check is one membership test.

GUARANTEE(test_an_erased_person_stays_erased)
"""

from __future__ import annotations

import hashlib
import hmac
import unicodedata
from typing import Any

from sqlalchemy import text


def normalise_name(name: str) -> str:
    """Unicode normalisation, casefold, whitespace collapse (pure, deterministic)."""
    collapsed = " ".join(name.split())
    return unicodedata.normalize("NFKC", collapsed).casefold()


def subject_hash(value: str, salt: bytes) -> str:
    """The salted HMAC-SHA256 of one subject value (pure)."""
    if not value.strip():
        raise ValueError("empty subject value")
    return hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()


def subject_hashes(*, profile_url: str | None, name: str | None, salt: bytes) -> list[str]:
    """The hashes a subject is suppressed by (url and normalised name; pure)."""
    out: list[str] = []
    if profile_url:
        out.append(subject_hash(profile_url.strip().casefold(), salt))
    if name:
        out.append(subject_hash(normalise_name(name), salt))
    if not out:
        raise ValueError("a subject needs a profile URL or a name")
    return out


async def suppress(
    s: Any, *, profile_url: str | None, name: str | None, salt: bytes, reason: str
) -> int:
    """Add a subject to the suppression list (insert-only, idempotent)."""
    n = 0
    for kind, value in (
        ("url", profile_url.strip().casefold() if profile_url else None),
        ("name", normalise_name(name) if name else None),
    ):
        if not value:
            continue
        await s.execute(
            text(
                "INSERT INTO suppression_entries (kind, hash, reason)"
                " VALUES (:k, :h, :r) ON CONFLICT (kind, hash) DO NOTHING"
            ),
            {"k": kind, "h": subject_hash(value, salt), "r": reason},
        )
        n += 1
    return n


async def suppressed(s: Any) -> set[tuple[str, str]]:
    """Every (kind, hash) on the list."""
    rows = (await s.execute(text("SELECT kind, hash FROM suppression_entries"))).all()
    return {(r.kind, r.hash) for r in rows}


def is_suppressed(
    known: set[tuple[str, str]], *, profile_url: str | None, name: str | None, salt: bytes
) -> bool:
    """Whether a subject is suppressed (pure; the pipelines' pre-store check)."""
    if profile_url and ("url", subject_hash(profile_url.strip().casefold(), salt)) in known:
        return True
    if name and ("name", subject_hash(normalise_name(name), salt)) in known:
        return True
    return False
