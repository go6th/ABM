"""Load the ABM session methods verbatim into each ABM model system prompt."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from account_intelligence.config import CONFIG_DIR

PROMPT_INSTRUCTION = (
    "Before any ABM task, read references/session-environment.md in full. It describes the "
    "execution environment, tool habits, and standing user rules this run depends on. Where it "
    "overlaps with the skill's reference files, the skill files win. The document fills the gaps "
    "around them. Never summarize or truncate it."
)
_REQUIRED_SECTIONS = (
    "## 4. Standing user rules injected on claude.ai",
    "## 5. Answering questions against an existing workspace",
    "## 6. Parity checklist for any new deployment",
)


@lru_cache(maxsize=8)
def _load_session_environment(config_dir: str) -> str:
    """Read and validate the full companion document; never return a truncated copy."""
    path = Path(config_dir) / "skill" / "archive" / "references" / "session-environment.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("ABM session environment reference is unavailable") from exc
    missing = tuple(section for section in _REQUIRED_SECTIONS if section not in text)
    if missing:
        raise RuntimeError("ABM session environment reference is incomplete")
    return text


def session_environment_context(task_kind: str, config_dir: Path | None = None) -> str:
    """Return the unmodified companion document for ABM model calls only."""
    if not task_kind.startswith("abm_"):
        return ""
    text = _load_session_environment(str(config_dir or CONFIG_DIR))
    return f"{PROMPT_INSTRUCTION}\n\n{text}"
