"""Fences around untrusted page text in research prompts (audit 2026-11-03).

Each stored payload's text reaches the research agents (Pass 1, Pass 2, People) inside its own
``<payload id="...">`` element, the way file extraction fences documents in ``<document>``
(``documents.extract``); the prompts say that whatever sits inside a ``<payload>`` element is data,
never instructions. A page cannot close or open the fence itself: ``<payload`` and ``</payload``
inside its text are neutralised (``&lt;``), so a model quoting the page quotes the same words.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TAG = re.compile(r"<(/?payload)", re.I)
_FENCE = re.compile(r'\A<payload id="[^"]*">\n(.*)\n</payload>\Z', re.S)


def _attribute(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def fence(payload_id: str, text: str) -> str:
    """One page text inside its ``<payload>`` element, any payload tag in it neutralised."""
    body = _TAG.sub(r"&lt;\1", text)
    return f'<payload id="{_attribute(payload_id)}">\n{body}\n</payload>'


def unfence(text: str) -> str:
    """The page text inside a fence (the text unchanged when it is not fenced); for the hermetic
    fake model, which reads the payload text as a real model would."""
    match = _FENCE.match(text)
    return match.group(1) if match else text


def fence_payloads(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of an agent input whose ``payloads`` texts are fenced (other fields as they are)."""
    out = dict(payload)
    items = payload.get("payloads")
    if isinstance(items, list | tuple):
        out["payloads"] = [
            {**item, "text": fence(str(item.get("payload_ref") or ""), str(item["text"]))}
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
            else item
            for item in items
        ]
    return out
