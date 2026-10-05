"""Versioned prompt files (workflow spec 2.4). The run records each prompt's content hash with
``graph_version``; no prompt text is ever logged or put in the checkpoint.

Prompts are plain files next to this module (``<name>.v<n>.md``). ``{canary}`` is replaced at
call time with the run's canary marker (an extractor output that echoes it is hostile, G14/J12).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=32)
def prompt(name: str) -> str:
    """The text of ``<name>.md`` (e.g. ``planner.v1``)."""
    return (PROMPTS / f"{name}.md").read_text()


def prompts_hash() -> str:
    """SHA-256 over every prompt file's name and bytes, in name order."""
    digest = hashlib.sha256()
    for path in sorted(PROMPTS.glob("*.md")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()
