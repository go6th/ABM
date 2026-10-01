"""Temporal workflows and activities (roadmap s6 workflow engine decision, s12 orchestration)."""

from app.workflows.hello import HelloInput, HelloResult, HelloWorkflow, hello_activity

WORKFLOWS = [HelloWorkflow]
ACTIVITIES = [hello_activity]

__all__ = [
    "ACTIVITIES",
    "WORKFLOWS",
    "HelloInput",
    "HelloResult",
    "HelloWorkflow",
    "hello_activity",
]
