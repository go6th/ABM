"""Prompt caching hygiene (R10). The cached prefix holds only static bytes: the versioned system
prompt and stable reference text, in deterministic form (canonical JSON, tools sorted by name, no
timestamps or ids); run and tenant data go after it, in the user turn. ``build`` refuses a prefix
that contains a run-specific marker. ``cache_ratio`` is computed on cacheable calls only.

GUARANTEE(test_prefix_bytes_are_deterministic_and_carry_no_tenant_data): R10.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from llm.models import Usage

UUIDISH = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
TIMESTAMPISH = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class PrefixNotStatic(ValueError):
    """The cached prefix would contain run or tenant data."""


def canonical(value: Any) -> str:
    """Canonical JSON (sorted keys, fixed separators)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build(system_prompt: str, tools: Sequence[Mapping[str, Any]] = (), reference: str = "") -> str:
    """The deterministic prefix; ``PrefixNotStatic`` when it carries an id or a timestamp."""
    parts = [system_prompt.strip()]
    if tools:
        parts.append(canonical(sorted(tools, key=lambda t: str(t.get("name")))))
    if reference:
        parts.append(reference.strip())
    prefix = "\n\n".join(parts)
    if UUIDISH.search(prefix) or TIMESTAMPISH.search(prefix):
        raise PrefixNotStatic("the cached prefix must not contain ids or timestamps")
    return prefix


def cache_ratio(usages: Iterable[tuple[bool, Usage]]) -> float | None:
    """cache-read / (cache-read + cache-write + uncached) over cacheable calls; None without any."""
    read = total = 0
    for cacheable, u in usages:
        if not cacheable:
            continue
        read += u.cache_read_tokens
        total += u.cache_read_tokens + u.cache_write_tokens + u.uncached_input_tokens
    return None if total == 0 else read / total
