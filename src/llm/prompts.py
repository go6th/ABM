"""Versioned prompts for routed calls: the existing loader (``agents.prompts``, one implementation)
plus each prompt's own content hash, pinned on every call record (R7). The verifier's prompts live
in ``src/verify/prompts`` and are loaded through ``prompt_from``.

GUARANTEE(test_prompt_hash_changes_iff_the_prompt_changes): R7.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from account_intelligence.agents.prompts import PROMPTS, prompt


def prompt_from(directory: Path, name: str) -> str:
    """The text of ``<directory>/<name>.md``."""
    return (directory / f"{name}.md").read_text()


def prompt_hash(text: str) -> str:
    """sha256 of the prompt's bytes."""
    return hashlib.sha256(text.encode()).hexdigest()


def load(name: str, directory: Path | None = None) -> tuple[str, str]:
    """``(text, hash)`` of a prompt from the agents' folder or ``directory``."""
    text = (
        prompt(name) if directory is None or directory == PROMPTS else prompt_from(directory, name)
    )
    return text, prompt_hash(text)
