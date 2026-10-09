"""Generate the observability Terraform JSON (alerts and dashboards) from config/telemetry
(D-56, O14). Deterministic; ``--check`` exits 1 when a generated file is stale."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from obs import alerts, dashboards  # noqa: E402
from obs.catalog import load_catalog  # noqa: E402

OUT = ROOT / "infra/modules/observability"  # the module root: Terraform loads only it
TELEMETRY = ROOT / "config/telemetry"
RUNBOOKS = ROOT / "docs/runbooks"


def render() -> dict[str, str]:
    """File name to content."""
    catalog = load_catalog()
    loaded = alerts.load(catalog, TELEMETRY, RUNBOOKS)
    return {
        "generated_alerts.tf.json": json.dumps(alerts.terraform(loaded), sort_keys=True, indent=1)
        + "\n",
        "generated_dashboards.tf.json": json.dumps(
            dashboards.render(catalog, TELEMETRY), sort_keys=True, indent=1
        )
        + "\n",
    }


def main() -> int:
    """Write or check."""
    files = render()
    stale = [
        n for n, body in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != body
    ]
    if "--check" in sys.argv:
        for n in stale:
            print(f"stale: {OUT / n}")
        return 1 if stale else 0
    for n, body in files.items():
        (OUT / n).write_text(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
