"""The owner's research skill as context for every model call (operator request 2026-10-16).

The skill archive (``config/skill/archive``: SKILL.md and its references, version 1.11) is the
owner's own description of how an account is researched: the rules that matter most, the
lessons learned run by run, the sense check, the writing rules and the grading scheme. Each
agent's system prompt carries the parts relevant to its step, so the model works from the
owner's concept and not only from the short agent prompt:

* every agent: SKILL.md "Rules that matter most";
* extraction (pass 1, pass 2, people): the lessons log and the signal tiers;
* the sense check: the lessons log and the phase 2 sense-check step;
* distill: the output spec (writing rules, grades, section order) and the lessons log.

Data and enrichment tool names are replaced by "6th agents", credit language by budget language
and guarded person names by "the owner" before the text is used (G9: a client-bound output that
carries any of them is rejected whole, and a model repeats what it reads);
the text is bounded (``MAX_CHARS``) and loaded once per process. The system block is what the
model provider caches, so the context costs its tokens once per cache window, not per call.
A missing archive gives an empty context: the run still works from the agent prompts alone.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from account_intelligence.config import CONFIG_DIR

ARCHIVE_DIR = CONFIG_DIR / "skill" / "archive"
MAX_CHARS = 48_000
# the data and enrichment tools the skill names (its own assets/strip_tool_names.py list) and
# the platform's provider terms; every one becomes "6th agents"
TOOL_NAMES = ("BitScale", "PredictLeads", "Sumble", "Firecrawl", "Parallel Search", "Parallel")
REPLACEMENT = "6th agents"

# which parts each task kind reads: (file, section heading or None for the whole file)
SECTIONS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "abm_setup": (("SKILL.md", "Rules that matter most"),),
    "abm_pass1_extract": (
        ("SKILL.md", "Rules that matter most"),
        ("references/lessons.md", None),
        ("references/signals.md", None),
    ),
    "abm_pass2_extract": (
        ("SKILL.md", "Rules that matter most"),
        ("references/lessons.md", None),
        ("references/signals.md", None),
    ),
    "abm_people_extract": (
        ("SKILL.md", "Rules that matter most"),
        ("references/lessons.md", None),
    ),
    "abm_sense_check": (
        ("SKILL.md", "Rules that matter most"),
        ("references/phase2-build.md", None),
        ("references/lessons.md", None),
    ),
    "abm_distill": (
        ("SKILL.md", "Rules that matter most"),
        ("references/output-spec.md", None),
        ("references/lessons.md", None),
    ),
}


def strip_tool_names(text: str, extra: tuple[str, ...] = ()) -> str:
    """The text with every data or enrichment tool name replaced by "6th agents"."""
    for name in (*TOOL_NAMES, *extra):
        if name.strip():
            text = re.sub(
                r"(?<!\w)" + re.escape(name.strip()) + r"(?!\w)", REPLACEMENT, text, flags=re.I
            )
    return text.replace(f"{REPLACEMENT}, {REPLACEMENT}", REPLACEMENT)


def scrub_client_terms(text: str) -> str:
    """The text with what a client-bound output may never carry replaced: credit counts and
    cost language become budget language, and every guarded person name (the owner's, the
    client's people) becomes "the owner" (``agents.naming.digest_hits``)."""
    from agents.naming import digest_hits

    text = re.sub(r"(?<!\w)credits?(?!\w)", "budget", text, flags=re.I)
    offsets = digest_hits(text)
    for start in sorted(offsets, reverse=True):
        end = start
        # LOOP(guarded name span): cap=the word's length; exit=the first non-letter
        while end < len(text) and (text[end].isalpha() or text[end] in "'-"):
            end += 1
        text = text[:start] + "the owner" + text[end:]
    return text


def section(text: str, heading: str | None) -> str:
    """One ``## heading`` section of a markdown file (to the next ``## ``), or the whole text."""
    if heading is None:
        return text.strip()
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return (f"## {heading}\n" + match.group(1).strip()) if match else ""


def _provider_terms() -> tuple[str, ...]:
    from agents.naming import provider_terms

    try:
        return provider_terms()
    except Exception:  # noqa: BLE001 - no provider catalogue: the skill's own list still applies
        return ()


@lru_cache(maxsize=16)
def skill_context(task_kind: str, archive: Path | None = None) -> str:
    """The skill text for one task kind, tool names stripped and bounded; "" without an archive
    or for a task kind the skill does not cover."""
    base = archive or ARCHIVE_DIR
    parts: list[str] = []
    for file, heading in SECTIONS.get(task_kind, ()):
        path = base / file
        if not path.is_file():
            continue
        body = section(path.read_text(encoding="utf-8"), heading)
        if body:
            parts.append(f"<!-- {file} -->\n{body}")
    if not parts:
        return ""
    text = scrub_client_terms(strip_tool_names("\n\n".join(parts), _provider_terms()))
    return text[:MAX_CHARS]


def with_skill_context(prompt_text: str, task_kind: str) -> str:
    """The agent prompt followed by the owner's skill context for this step, or the prompt alone."""
    context = skill_context(task_kind)
    if not context:
        return prompt_text
    return (
        f"{prompt_text}\n\nThe owner's research skill (follow it; it is the standard every file"
        f" is judged by):\n\n{context}"
    )
