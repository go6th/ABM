"""Ratchet: the modules under ``src/`` that read YAML themselves (``yaml.safe_load``).

Configuration is meant to be read once through ``account_intelligence.config_files`` and the
per-package ``config`` modules; a new ad hoc reader (the 2026-10-21 "download everything"
freeze was ``providers.yaml`` parsed per table cell) must be a deliberate addition here. A reader
that goes away is removed from the list so it never hides a later one.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# measured 2026-10-22; ratchet: shrink, never grow
READERS: frozenset[str] = frozenset(
    {
        "account_intelligence.api.routes.admin_console",
        "account_intelligence.api.routes.runs",
        "account_intelligence.chat.registry",
        "account_intelligence.config_files",
        "account_intelligence.connectors.cache",
        "account_intelligence.connectors.settings",
        "account_intelligence.grading.config",
        "account_intelligence.grading.judge",
        "account_intelligence.model_routing",
        "account_intelligence.platform.llm",
        "account_intelligence.platform.prompt_cache",
        "account_intelligence.rag.config",
        "account_intelligence.replica.credits",
        "account_intelligence.replica.isolation",
        "account_intelligence.replica.jobs",
        "account_intelligence.replica.live",
        "account_intelligence.replica.usage_estimate",
        "account_intelligence.tracker.config",
        "account_intelligence.workflow.handlers",
        "account_intelligence.workflow.spec",
        "admin.support",
        "agents.config",
        "agents.contracts",
        "agents.naming",
        "agents.production.provider",
        "alerts.rules",
        "audit.events",
        "connectors_client.scopes",
        "contracts.monitor",
        "crm_writeback.service",
        "crosscut.conformance",
        "datamodel.pins",
        "documents.config",
        "documents.estimate",
        "documents.tiers",
        "evalgate.config",
        "evalgate.golden",
        "exports.config",
        "governance.monitoring_budget",
        "identity.local_password",
        "identity.rate_limit",
        "identity.roles",
        "identity.sessions",
        "jobs.config",
        "llm.choice",
        "llm.config",
        "llm.reply_cache",
        "metering.limits",
        "obs.alerts",
        "obs.catalog",
        "obs.dashboards",
        "parallel.config",
        "people.config",
        "people.technology",
        "pilot.feedback",
        "privacy.dsar",
        "privacy.minimise",
        "privacy.retention",
        "quality.regression",
        "refresh.policy",
        "research_files.config",
        "signals.score",
        "spend.config",
        "tenancy.lifecycle",
        "tenancy.settings",
        "verify.config",
        "views.branding",
        "views.query",
        "views.saved",
    }
)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _reads_yaml(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "safe_load"
            and isinstance(func.value, ast.Name)
            and func.value.id == "yaml"
        ):
            return True
        if isinstance(func, ast.Name) and func.id == "safe_load":
            return True
    return False


def _readers() -> set[str]:
    return {
        _module_name(path)
        for path in sorted(SRC.rglob("*.py"))
        if _reads_yaml(ast.parse(path.read_text(), filename=str(path)))
    }


def test_yaml_readers_never_grow() -> None:
    readers = _readers()
    new = sorted(readers - READERS)
    assert not new, f"new yaml.safe_load callers (read through a config module instead): {new}"


def test_the_reader_list_carries_no_dead_entries() -> None:
    gone = sorted(READERS - _readers())
    assert not gone, f"modules no longer read YAML; drop them from READERS: {gone}"
