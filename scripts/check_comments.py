"""Section 10: every file explains itself. Exit 1 listing any file without its header.

Python (src, scripts, tests, migrations): a module docstring. SQL migrations: a leading ``--``
comment. YAML config: a leading ``#`` comment. Public docstrings are checked by ``interrogate``
(100%, settings in pyproject.toml).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def missing() -> list[str]:
    """Files without their header comment or docstring."""
    out: list[str] = []
    for top in ("src", "scripts", "tests", "migrations"):
        for path in sorted((ROOT / top).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if not ast.get_docstring(ast.parse(path.read_text())):
                out.append(str(path.relative_to(ROOT)))
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        if not path.read_text().lstrip().startswith("--"):
            out.append(str(path.relative_to(ROOT)))
    for path in sorted((ROOT / "config").glob("*.yaml")):
        if not path.read_text().lstrip().startswith("#"):
            out.append(str(path.relative_to(ROOT)))
    return out


def main() -> int:
    """Print what is missing; exit 1 when anything is."""
    gaps = missing()
    for gap in gaps:
        print(f"no header comment: {gap}")
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
