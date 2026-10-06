"""Dashboards as code (D-56): ``config/telemetry/dashboards.yaml`` rendered to Cloud Monitoring
dashboard JSON (one tile per catalog metric). A panel naming a metric outside the catalog fails.

GUARANTEE(test_dashboards_reference_catalog_metrics_only): O14.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from obs.catalog import Catalog
from obs.errors import CatalogInvalid


def render(catalog: Catalog, telemetry_dir: Path) -> dict[str, Any]:
    """Terraform JSON: one ``google_monitoring_dashboard`` per dashboard ([VERIFY] V-OBS-2)."""
    doc = yaml.safe_load((telemetry_dir / "dashboards.yaml").read_text())
    resources: dict[str, Any] = {}
    for name, spec in sorted(doc["dashboards"].items()):
        tiles = []
        prefix = "workload.googleapis.com/"
        for i, metric in enumerate(spec["panels"]):
            if metric not in catalog.metrics:
                raise CatalogInvalid(f"dashboard {name}: {metric} is not in the catalog")
            tiles.append(
                {
                    "xPos": (i % 2) * 6,
                    "yPos": (i // 2) * 4,
                    "width": 6,
                    "height": 4,
                    "widget": {
                        "title": metric,
                        "xyChart": {
                            "dataSets": [
                                {
                                    "timeSeriesQuery": {
                                        "timeSeriesFilter": {
                                            "filter": f'metric.type="{prefix}{metric}"'
                                        }
                                    }
                                }
                            ]
                        },
                    },
                }
            )
        body = {"displayName": f"ai-{name}", "mosaicLayout": {"columns": 12, "tiles": tiles}}
        import json

        resources[name] = {
            "project": "${var.project_id}",
            "dashboard_json": json.dumps(body, sort_keys=True),
        }
    return {"resource": {"google_monitoring_dashboard": resources}}
