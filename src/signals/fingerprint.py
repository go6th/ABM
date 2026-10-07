"""Event fingerprints (DS1, D-73): deterministic identity of an event from its type, normalised
entity, normalised attributes and a date bucket. The same event from a second outlet, a re-fetch,
a replayed webhook or a later probe gets the same fingerprint. Pure.

GUARANTEE(test_fingerprints_are_deterministic_and_outlet_independent)
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import date

_SUFFIXES = re.compile(r"\b(inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|gmbh|ag|sa)\b")


def normalise(text: str) -> str:
    """Case-folded, accent-free, punctuation-free, company suffixes removed, spaces collapsed."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = _SUFFIXES.sub(" ", t)
    return " ".join(t.split())


def date_bucket(d: date, days: int) -> int:
    """The bucket number of a date (``days``-wide buckets from the epoch)."""
    if days <= 0:
        raise ValueError("bucket width must be positive")
    return d.toordinal() // days


def fingerprint(
    event_type: str, entity: str, attributes: Mapping[str, str], when: date, *, bucket_days: int
) -> str:
    """sha256 over the canonical JSON of the normalised parts (outlet and URL never included)."""
    canon = {
        "type": event_type,
        "entity": normalise(entity),
        "attrs": {k: normalise(v) for k, v in sorted(attributes.items())},
        "bucket": date_bucket(when, bucket_days),
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()
