"""The owner's research skill as context for every model call (operator request 2026-10-16).

The skill archive (``config/skill/archive``: SKILL.md and its references) is the
owner's own description of how an account is researched: the rules that matter most, the
lessons learned run by run, the sense check, the writing rules and the grading scheme. Each
agent's system prompt carries the parts relevant to its step, so the model works from the
owner's concept and not only from the short agent prompt:

* every agent: SKILL.md "Rules that matter most";
* extraction (pass 1, pass 2, people): the lessons log and the signal tiers;
* the sense check: the lessons log and the phase 2 sense-check step;
* distill: the output spec (writing rules, grades, section order) and the lessons log;
* Ask (a question about a workspace, owner request 2026-10-19): the rules that matter most,
  the checklist's standing requirements and scope defaults, and the companion's standing rules
  and its rules for answering against an existing workspace (``ask_skill_context``).

Skill references have data and enrichment tool names (the provider configuration's vendor names)
replaced by "6th agents", credit language by budget language and guarded person names by "the
owner" before use (G9: client-bound output is rejected if it carries them). The companion
session-environment reference is injected verbatim because its complete standing rules and
methods must reach every ABM model call. Both are loaded once per process; the model provider
caches the system block, so the context costs its tokens once per cache window, not per call.
Known ABM calls fail closed if the archive, a required skill file, or the companion reference is
missing. Unknown task kinds are left untouched.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from account_intelligence.config import CONFIG_DIR

ARCHIVE_DIR = CONFIG_DIR / "skill" / "archive"
MAX_CHARS = 48_000
SESSION_ENVIRONMENT_MAX_CHARS = 48_000
ASSEMBLED_PROMPT_MAX_CHARS = 100_000
SESSION_ENVIRONMENT_DIRECTIVE = (
    "Before any ABM task, read references/session-environment.md in full. Where it overlaps "
    "with the skill's reference files, the skill files win. Never summarize or truncate it. "
    "The skill's documented path /home/claude/abm-account-research/ maps to the bundled "
    "runtime directory config/skill/archive/; resolve relative references/, assets/ and "
    "scripts/ paths from that directory."
)
# every data and enrichment tool the skill names becomes "6th agents": the vendor names come
# from the provider configuration (the one place they may live), never from code
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

# what Ask reads for every answer about a workspace: the skill's rules and its standing
# requirements, then the companion's standing rules and its workspace answering rules (sections 4
# and 5; sections 2 and 3 describe a research run's sandbox and tools, which Ask does not have)
ASK_SECTIONS: tuple[tuple[str, str | None], ...] = (
    ("SKILL.md", "Rules that matter most"),
    ("references/checklist.md", "1. Standing requirements from the user"),
    ("references/checklist.md", "2. Scope defaults"),
    ("references/session-environment.md", "4. Standing user rules injected on claude.ai"),
    ("references/session-environment.md", "5. Answering questions against an existing workspace"),
)
ASK_DIRECTIVE = (
    "The owner's research skill applies to every answer about an account; follow it. Where the"
    " companion's standing rules and the checklist disagree (for example on the platform lanes),"
    " the checklist wins. In this workspace, computing from the files means the `describe_table`"
    " and `query_table` tools when they are offered, or counting over a whole file that is"
    " provided; never count from passages. The answer rules above (citations, the full table)"
    " still apply; the skill's pull, build and delivery steps describe research runs, not"
    " answers."
)


@lru_cache(maxsize=1)
def tool_names() -> tuple[str, ...]:
    """The vendor names of the configured providers, longest first ("Parallel Search" before
    "Parallel"), plus each name followed by "Search"; "" when no provider config is readable."""
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    try:
        names = {n.strip() for n in vendor_names(load_providers_config()).values() if n.strip()}
    except Exception:  # noqa: BLE001 - no provider config: the provider terms still apply
        return ()
    names |= {f"{n} Search" for n in names}
    # "Parallel" is also ordinary English ("in parallel", "parallel processing"). The
    # provider's compound display name remains matchable without rewriting those phrases.
    names.discard("Parallel")
    return tuple(sorted(names, key=len, reverse=True))


def strip_tool_names(text: str, extra: tuple[str, ...] = ()) -> str:
    """The text with every data or enrichment tool name replaced by "6th agents"."""
    for name in (*tool_names(), *extra):
        if name.strip():
            text = re.sub(
                r"(?<!\w)" + re.escape(name.strip()) + r"(?!\w)", REPLACEMENT, text, flags=re.I
            )
    return re.sub(
        rf"\b{re.escape(REPLACEMENT)}\b(?:\s*(?:,|and)\s*\b{re.escape(REPLACEMENT)}\b)+",
        REPLACEMENT,
        text,
        flags=re.I,
    )


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
    """The skill text for one covered task, tool names stripped and bounded.

    Missing files for a covered task raise ``FileNotFoundError``. An unknown task kind returns
    an empty string.
    """
    if task_kind not in SECTIONS:
        return ""
    return _assemble(archive or ARCHIVE_DIR, SECTIONS[task_kind], task_kind)[:MAX_CHARS]


@lru_cache(maxsize=4)
def ask_skill_context(archive: Path | None = None) -> str:
    """The skill text every Ask answer follows (``ASK_SECTIONS``), tool names and guarded names
    stripped like every other skill reference. Fails closed: a missing file or section raises,
    and so does a text too long to send whole."""
    text = f"{ASK_DIRECTIVE}\n\n" + _assemble(
        archive or ARCHIVE_DIR, ASK_SECTIONS, "ask", strict=True
    )
    if len(text) > MAX_CHARS:
        raise ValueError(f"Ask skill context is {len(text)} characters; refusing to truncate it")
    return text


def _assemble(
    base: Path, sections: tuple[tuple[str, str | None], ...], task: str, *, strict: bool = False
) -> str:
    """The named sections of the archive's files, in order, scrubbed for client use; ``strict``
    raises on any missing section instead of only on an empty result."""
    parts: list[str] = []
    for file, heading in sections:
        path = base / file
        if not path.is_file():
            raise FileNotFoundError(f"required ABM skill file is missing: {path}")
        body = section(path.read_text(encoding="utf-8"), heading)
        if not body and strict:
            raise ValueError(f"required ABM skill section is missing: {file}, {heading}")
        if body:
            parts.append(f"<!-- {file} -->\n{body}")
    if not parts:
        raise ValueError(f"required ABM skill context is empty for task: {task}")
    return scrub_client_terms(strip_tool_names("\n\n".join(parts), _provider_terms()))


@lru_cache(maxsize=8)
def session_environment_context(archive: Path | None = None) -> str:
    """Read the ABM session appendix unchanged and in full, failing closed if it is absent or
    too large to send without truncation. Unlike skill references, this file is not scrubbed:
    the owner requires the 21 standing rules to reach the model verbatim."""
    base = archive or ARCHIVE_DIR
    path = base / "references" / "session-environment.md"
    if not path.is_file():
        raise FileNotFoundError(f"required ABM context is missing: {path}")
    text = path.read_bytes().decode("utf-8")
    if len(text) > SESSION_ENVIRONMENT_MAX_CHARS:
        raise ValueError(f"ABM session context is {len(text)} characters; refusing to truncate it")
    return text


def with_skill_context(prompt_text: str, task_kind: str) -> str:
    """The session appendix, then this step's skill context, follow every ABM agent prompt."""
    context = skill_context(task_kind)
    if not context:
        return prompt_text
    session = session_environment_context()
    assembled = (
        f"{prompt_text}\n\n{SESSION_ENVIRONMENT_DIRECTIVE}\n\n"
        f"<!-- references/session-environment.md, verbatim -->\n{session}\n\n"
        f"The owner's research skill (follow it; it is the standard every file"
        f" is judged by):\n\n{context}"
    )
    if len(assembled) > ASSEMBLED_PROMPT_MAX_CHARS:
        raise ValueError(
            f"assembled ABM prompt is {len(assembled)} characters; limit is "
            f"{ASSEMBLED_PROMPT_MAX_CHARS}"
        )
    return assembled
