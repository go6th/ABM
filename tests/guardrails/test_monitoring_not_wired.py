"""The monitoring product (``refresh``, ``signals``, ``alerts``) is built but not wired (ADR-0134).

Only the importers listed in ``ALLOWED`` may import those packages from elsewhere in ``src/``, and
each one must be named in ADR-0134. Wiring monitoring into a route, the replica screens, a
scheduler tick or the worker means updating the ADR and this list in the same change.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
ADR = ROOT / "docs" / "decisions" / "ADR-0134-monitoring-product-not-wired.md"
MONITORING = ("refresh", "signals", "alerts")

# importer module -> the monitoring modules it may import (ADR-0134, decision 2)
ALLOWED: dict[str, frozenset[str]] = {
    "account_intelligence.api.routes.signals": frozenset(
        {"alerts.feedback", "alerts.rules", "alerts.subscriptions", "refresh.history"}
    ),
    "crm_writeback.payloads": frozenset({"alerts.lint", "alerts.sensitive"}),
    "documents.workspace_docx": frozenset({"signals.events"}),
}


def _module(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")


def _monitoring_imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            base = node.module
            names = [f"{base}.{a.name}" for a in node.names] if base in MONITORING else [base]
        else:
            continue
        found |= {n for n in names if n.split(".", 1)[0] in MONITORING}
    return found


def _importers() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).parts
        if rel[0] in MONITORING:
            continue
        got = _monitoring_imports(ast.parse(path.read_text(), filename=str(path)))
        if got:
            out[_module(path)] = got
    return out


def test_only_the_listed_modules_import_the_monitoring_product() -> None:
    extra = {
        module: sorted(names - ALLOWED.get(module, frozenset()))
        for module, names in _importers().items()
        if names - ALLOWED.get(module, frozenset())
    }
    assert not extra, (
        f"the not-wired monitoring product gained an importer; update ADR-0134 and ALLOWED: {extra}"
    )


def test_no_api_route_but_signals_and_no_replica_module_imports_it() -> None:
    importers = _importers()
    for module in importers:
        assert not module.startswith("account_intelligence.replica"), module
        if module.startswith("account_intelligence.api.routes."):
            assert module == "account_intelligence.api.routes.signals", module


def test_every_allowed_importer_is_named_in_the_adr() -> None:
    adr = ADR.read_text()
    for module, names in ALLOWED.items():
        assert module in adr, module
        for name in names:
            assert name in adr, name


def test_each_package_says_it_is_not_wired() -> None:
    for pkg in MONITORING:
        doc = ast.get_docstring(ast.parse((SRC / pkg / "__init__.py").read_text())) or ""
        assert doc.startswith("NOT WIRED — see ADR-0134"), pkg
