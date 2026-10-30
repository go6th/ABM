"""Telemetry contract check (O5, O14): every metric created in code and every literal span name is
declared in the catalogs; tracer, meter and logger acquisition happens only in ``src/obs`` and the
designated ``obs.py`` edge modules; ``print`` only in command-line modules. Exit 1 on any gap."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from obs.catalog import load_catalog  # noqa: E402

CLI = {
    "src/people/docs.py",
    "src/exports/docs.py",
    "src/account_intelligence/grading/docs.py",
    "src/spend/jobs.py",
    "src/evalgate/cli.py",
    "src/account_intelligence/worker/__main__.py",
    "src/account_intelligence/config.py",
    "src/parity/gate/__main__.py",
    "src/parity/render.py",
    "src/parity/workspace.py",
}
CREATE = {
    "create_counter",
    "create_histogram",
    "create_gauge",
    "create_up_down_counter",
    "create_observable_gauge",
}


def problems() -> list[str]:
    """Every contract gap."""
    cat = load_catalog()
    out: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if "/rag/" in rel:
            continue  # owned by another work package (its metrics join the catalog with it)
        edge = rel.startswith("src/obs/") or path.name == "obs.py"
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            first = node.args[0] if node.args else None
            literal = first.value if isinstance(first, ast.Constant) else None
            if name in CREATE and literal is not None and literal not in cat.metrics:
                out.append(f"{rel}:{node.lineno}: metric {literal} not in the catalog")
            if (
                name in ("start_as_current_span", "start_span")
                and isinstance(literal, str)
                and literal not in cat.spans
            ):
                out.append(f"{rel}:{node.lineno}: span {literal} not in the catalog")
            if name in ("get_tracer", "get_meter", "getLogger") and not edge:
                out.append(f"{rel}:{node.lineno}: {name} outside src/obs and obs.py edges")
            if name == "print" and rel not in CLI:
                out.append(f"{rel}:{node.lineno}: print outside a command-line module")
    return out


def main() -> int:
    """Print gaps; exit 1 when any."""
    gaps = problems()
    for g in gaps:
        print(g)
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
