"""Sensitive findings (AL5, D-76): breach, layoff and lawsuit events, detected by event category
in code, rendered only from fixed templates labelled as preparation, never in an immediate
subject line and never offered as a hook. Pure.

GUARANTEE(test_sensitive_events_render_only_from_fixed_templates)
"""

from __future__ import annotations

from typing import Literal

Category = Literal["breach", "layoff", "lawsuit"]
LABEL = "Preparation only, not an opening line."

# event types (from the extractor's typed outputs) mapped to the three sensitive categories
CATEGORY_OF: dict[str, Category] = {
    "data_breach": "breach",
    "security_incident": "breach",
    "ransomware": "breach",
    "layoff": "layoff",
    "workforce_reduction": "layoff",
    "plant_closure_with_job_cuts": "layoff",
    "lawsuit": "lawsuit",
    "litigation": "lawsuit",
    "class_action": "lawsuit",
}

_TEXT: dict[Category, str] = {
    "breach": "A security incident was reported ({source}). Review it before any contact so you "
    "are aware of the context; do not raise it.",
    "layoff": "A workforce reduction was reported ({source}). Review it before any contact so you "
    "are aware of the context; do not raise it.",
    "lawsuit": "A legal proceeding was reported ({source}). Review it before any contact so you "
    "are aware of the context; do not raise it.",
}


def category(event_type: str) -> Category | None:
    """The sensitive category of an event type, or None."""
    return CATEGORY_OF.get(event_type)


def render(cat: Category, source_code: str | None) -> str:
    """The only text a sensitive finding may carry, in every channel."""
    return f"{LABEL} {_TEXT[cat].format(source=source_code or 'source on file')}"


def subject(cat: Category) -> str:
    """A neutral digest subject; an immediate subject never names a sensitive event."""
    return "Account context update"
