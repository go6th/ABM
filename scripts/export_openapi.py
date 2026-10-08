"""OpenAPI snapshot (D-91 step 0): the contract the web client is generated from.

Builds the application with inert settings (no database is opened; ``create_app`` only declares
routes), takes ``app.openapi()`` and writes it to ``docs/api/openapi.json`` with sorted keys so
the file is stable. ``--check`` writes nothing and exits 1 when the committed snapshot differs
(the CI drift gate; ``tests/api/test_openapi_snapshot.py`` runs it).

Usage: ``python scripts/export_openapi.py [--check]``
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "docs" / "api" / "openapi.json"
# Inert values: settings are required at import time but nothing here connects anywhere.
INERT = {
    "DATABASE_URL": "postgresql+asyncpg://snapshot@127.0.0.1:1/snapshot",
    "CHECKPOINT_DATABASE_URL": "postgresql://snapshot@127.0.0.1:1/snapshot",
    "JWKS_URL": "https://snapshot.invalid/jwks",
    "JWT_AUDIENCE": "snapshot",
    "JWT_ISSUER": "snapshot",
}


def build() -> dict[str, Any]:
    """The application's OpenAPI document (worker ``/tasks`` routes are internal and left out)."""
    for key, value in INERT.items():
        os.environ.setdefault(key, value)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from account_intelligence.api.main import create_app

    doc: dict[str, Any] = create_app().openapi()
    doc["paths"] = {p: v for p, v in doc["paths"].items() if not p.startswith("/tasks/")}
    return doc


def render(doc: dict[str, Any]) -> str:
    """Stable text: sorted keys, two-space indent, trailing newline."""
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    """Write the snapshot, or with ``--check`` compare it."""
    text = render(build())
    if "--check" in argv:
        current = SNAPSHOT.read_text() if SNAPSHOT.exists() else ""
        if current != text:
            print("openapi snapshot drift: run python scripts/export_openapi.py", file=sys.stderr)
            return 1
        print("openapi snapshot OK")
        return 0
    SNAPSHOT.write_text(text)
    print(f"wrote {SNAPSHOT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
