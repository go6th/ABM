"""Model output handling shared by every LLM caller: find the JSON object in an answer.

Nothing is repaired: an answer without one valid JSON object is ``None`` and the caller makes at
most one bounded re-ask (workflow spec 3.7), then records the item as blocked or unclassified.
"""

from __future__ import annotations

import json
from typing import Any


def parse_json(text: str) -> Any:
    """The JSON object between the first ``{`` and the last ``}``, or ``None``."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
