"""The one routing table (``llm.route``): every model call site names a registered task kind (R2),
every registered task kind has a call site (no dead entries), every tier names a ``models.yaml``
task and a role, and no model id or model SDK appears outside configuration and the adapters."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from llm.prompts import load, prompt_hash
from llm.route import TASK_KINDS, TIERS, Tier, UnknownTaskKind, route

ROOT = Path(__file__).resolve().parents[2]
# where a task kind is passed: the callable name -> the positional index of the task kind
SITES = {"model": 0, "ask_json": 1, "LlmPostingClassifier": 1}
# configuration that names the task kind an agent or a mode calls with (``task_kind:`` entries)
CONFIGURED_SITES = ("config/agents.yaml", "config/research_input_modes.yaml")


def _call_sites() -> tuple[dict[str, list[str]], list[str], list[str]]:
    """``(task kind -> call sites, direct port calls, call sites without a literal kind)``."""
    found: dict[str, list[str]] = {}
    direct: list[str] = []
    bad: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        if "/rag/" in rel or rel == "src/llm/route.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if (
                name in ("complete", "complete_as")
                and isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "llm"
                and rel != "src/account_intelligence/workflow/deps.py"
            ):
                direct.append(f"{rel}:{node.lineno}")
            if name not in SITES or len(node.args) <= SITES[name]:
                continue
            arg = node.args[SITES[name]]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(arg.value, []).append(f"{rel}:{node.lineno}")
            elif not (isinstance(arg, ast.Name) and arg.id == "task_kind") and not (
                isinstance(arg, ast.Attribute) and arg.attr == "task_kind"
            ):
                bad.append(f"{rel}:{node.lineno} non-literal task kind")
    for rel in CONFIGURED_SITES:
        text = (ROOT / rel).read_text()
        for kind in re.findall(r"^\s*task_kind:\s*([a-z][a-z0-9_]*)", text, re.M):
            if kind != "null":  # a code-only agent (the pull layer has no model, G5)
                found.setdefault(kind, []).append(rel)
    return found, direct, bad


def test_every_model_call_site_names_a_registered_task_kind() -> None:
    """R2: no module but ``workflow.deps`` calls the model port; every call site passes a
    literal, registered task kind (or forwards its own ``task_kind`` parameter)."""
    found, direct, bad = _call_sites()
    unregistered = sorted(f"{k} at {v}" for k, v in found.items() if k not in TASK_KINDS)
    assert direct == [] and bad == [] and unregistered == []
    assert sum(len(v) for v in found.values()) >= 7


def test_every_registered_task_kind_has_a_call_site() -> None:
    """The table cannot grow dead entries: a task kind no code or agent calls is removed (the
    audit of 2026-11-03 found six registered and never called)."""
    found, _, _ = _call_sites()
    assert sorted(set(TASK_KINDS) - set(found)) == []


def test_every_tier_names_a_models_yaml_task_and_a_role() -> None:
    raw = yaml.safe_load((ROOT / "config/models.yaml").read_text())
    assert {t.task for t in TIERS.values()} == set(raw["tasks"]) == set(raw["task_settings"])
    assert {t for t, r in TIERS.items() if r.role == "research"} == {
        Tier.PLANNER,
        Tier.EXTRACTOR,
        Tier.CLASSIFIER,
    }
    for kind, spec in TASK_KINDS.items():
        r = route(kind)
        assert (r.tier, r.task, r.role, r.max_tokens) == (
            spec.tier,
            TIERS[spec.tier].task,
            TIERS[spec.tier].role,
            spec.max_tokens,
        )
        # the configured models think and thinking counts against max_tokens (2026-10-12)
        assert r.max_tokens >= 2000
        assert r.effort == raw["task_settings"][r.task]["effort"]


def test_an_unregistered_task_kind_is_refused() -> None:
    with pytest.raises(UnknownTaskKind):
        route("free_form")


MODEL_ID = re.compile(r"\b(claude|gpt|gemini|llama|mistral|grok)-[0-9a-z][0-9a-z.\-]*", re.I)


def test_no_model_id_outside_config() -> None:
    """R1: no model-id-shaped string in src, prompts or tests."""
    roots = [ROOT / "src", ROOT / "tests", ROOT / "evals"]
    offenders = [
        str(p.relative_to(ROOT))
        for r in roots
        for p in r.rglob("*")
        if p.suffix in (".py", ".md") and "/rag/" not in str(p) and MODEL_ID.search(p.read_text())
    ]
    assert offenders == []


def test_schema_validation_refuses_every_malformed_output() -> None:
    """R3: a reply that is not the schema's object is None (the caller re-asks once, then
    ``SchemaInvalid``); nothing unvalidated reaches a caller."""
    from pydantic import BaseModel, ConfigDict

    from llm.schema import validate

    class Out(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        answer: str

    for bad in ("not json", '{"answer": 3}', '{"other": "x"}', "[1, 2]", "{}"):
        assert validate(bad, Out) is None, bad
    fixed = validate('{"answer": "fixed"}', Out)
    assert fixed is not None and fixed.answer == "fixed"


def test_prompt_hash_changes_iff_the_prompt_changes() -> None:
    text, h = load("judge.v1")
    assert h == prompt_hash(text) and prompt_hash(text + " ") != h


def test_no_llm_client_import_outside_the_adapters() -> None:
    """Model SDKs are imported only by the Anthropic port (``platform/llm.py``) and its error
    mapping (``llm/adapters/anthropic_api.py``)."""
    allowed = {"src/llm/adapters/anthropic_api.py", "src/account_intelligence/platform/llm.py"}
    pattern = re.compile(r"^\s*(import|from)\s+(anthropic|openai|google\.genai|vertexai)\b", re.M)
    found = {
        str(p.relative_to(ROOT))
        for p in (ROOT / "src").rglob("*.py")
        if "/rag/" not in str(p) and pattern.search(p.read_text())
    }
    assert found <= allowed
