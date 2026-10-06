"""Alerts as code (O9, O10, O14, D-60): load and validate ``config/telemetry/alerts.yaml``,
generate Terraform JSON for Cloud Monitoring, and simulate the alert semantics on incident
timelines.

Validation: every alert names a catalog metric (or an SLO of ``slos.yaml``), a severity of
``severity.yaml``, an owner, an existing runbook under ``docs/runbooks`` and a source or status.

The simulator (``fires``) evaluates OUR configured semantics on a scripted timeline of samples; it
is not Google's evaluator ([VERIFY] V-OBS-2); the generated Terraform passes ``terraform validate``
against the google provider (``make obs-verify``).

GUARANTEE(test_every_alert_fires_on_its_incident_and_stays_quiet_when_healthy): O14.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from obs.catalog import Catalog
from obs.errors import CatalogInvalid
from obs.slo import Window, multiwindow_page

KINDS = frozenset({"burn", "any", "rate_above", "ratio_below", "threshold", "absent"})
WINDOW_S = {"5m": 300, "30m": 1800, "1h": 3600, "6h": 21600}


@dataclass(frozen=True)
class Alert:
    """One validated alert."""

    name: str
    kind: str
    severity: str
    owner: str
    runbook: str
    spec: Mapping[str, Any]


@dataclass(frozen=True)
class Sample:
    """One timeline sample: at ``t`` seconds the metric had ``value`` (counters: increments)."""

    t: float
    metric: str
    value: float
    labels: Mapping[str, str] = field(default_factory=dict)


def load(catalog: Catalog, telemetry_dir: Path, runbook_dir: Path) -> dict[str, Alert]:
    """Validate the alert catalog."""
    doc = yaml.safe_load((telemetry_dir / "alerts.yaml").read_text())
    slos = yaml.safe_load((telemetry_dir / "slos.yaml").read_text())["slos"]
    severities = yaml.safe_load((telemetry_dir / "severity.yaml").read_text())["severities"]
    out: dict[str, Alert] = {}
    for name, spec in doc["alerts"].items():
        kind = spec.get("kind")
        if kind not in KINDS:
            raise CatalogInvalid(f"alert {name}: unknown kind")
        if kind == "burn":
            if spec.get("slo") not in slos:
                raise CatalogInvalid(f"alert {name}: unknown SLO")
        elif spec.get("metric") not in catalog.metrics:
            raise CatalogInvalid(f"alert {name}: metric not in the catalog")
        if spec.get("severity") not in severities:
            raise CatalogInvalid(f"alert {name}: unknown severity")
        if not spec.get("owner"):
            raise CatalogInvalid(f"alert {name}: no owner")
        if "source" not in spec and "status" not in spec:
            raise CatalogInvalid(f"alert {name}: no source or status")
        runbook = str(spec.get("runbook", ""))
        if not runbook or not (runbook_dir / runbook).exists():
            raise CatalogInvalid(f"alert {name}: runbook {runbook!r} does not exist")
        out[str(name)] = Alert(
            str(name), str(kind), str(spec["severity"]), str(spec["owner"]), runbook, dict(spec)
        )
    return out


def _matches(sample: Sample, spec: Mapping[str, Any]) -> bool:
    for key in ("state", "degraded", "failure_class", "outcome"):
        if key in spec and sample.labels.get(key) != str(spec[key]):
            return False
    return not ("outcome_not" in spec and sample.labels.get("outcome") in spec["outcome_not"])


def fires(alert: Alert, samples: Sequence[Sample], at: float) -> bool:
    """Whether the alert fires at time ``at`` given the samples (our semantics)."""
    s = alert.spec
    if alert.kind == "burn":
        long_s, short_s = WINDOW_S[str(s["window"])], WINDOW_S[str(s["short"])]

        def window(span: float) -> Window:
            reqs = [
                x for x in samples if x.metric == "api_requests_total" and at - span < x.t <= at
            ]
            bad = sum(
                int(x.value) for x in reqs if x.labels.get("code_class") in ("5xx", "timeout")
            )
            return Window(bad, sum(int(x.value) for x in reqs))

        return multiwindow_page(
            window(long_s),
            window(short_s),
            Fraction("0.999"),
            Fraction(str(s["factor"])),
            int(s.get("min_events", 100)),
        )
    mine = [x for x in samples if x.metric == s.get("metric") and x.t <= at and _matches(x, s)]
    if alert.kind == "any":
        return any(x.value > 0 for x in mine)
    if alert.kind == "rate_above":
        span = float(s["window_s"])
        return sum(x.value for x in mine if at - span < x.t) > float(s["above"])
    if alert.kind == "ratio_below":
        span = float(s["window_s"])
        recent = [x for x in mine if at - span < x.t]
        volume = sum(float(x.labels.get("volume", "0")) for x in recent)
        if volume < float(s.get("min_volume", 0)) or not recent:
            return False
        return recent[-1].value < float(s["below"])
    if alert.kind == "threshold":
        streak: float | None = None
        for x in sorted(mine, key=lambda y: y.t):  # the current run of values above the bar
            above = x.value > float(s["above"])
            streak = (x.t if streak is None else streak) if above else None
        return streak is not None and at - streak >= float(s.get("for_s", 0))
    # absent: no sample for for_s seconds
    last = max((x.t for x in mine), default=None)
    return last is None or at - last > float(s["for_s"])


def terraform(alerts: Mapping[str, Alert]) -> dict[str, Any]:
    """Terraform JSON: one ``google_monitoring_alert_policy`` per alert (channels and project are
    variables; nothing is applied)."""
    resources: dict[str, Any] = {}
    for name in sorted(alerts):
        a = alerts[name]
        metric = a.spec.get("metric") or "api_requests_total"
        condition: dict[str, Any]
        if a.kind == "absent":
            condition = {
                "condition_absent": {
                    "filter": f'metric.type="workload.googleapis.com/{metric}"',
                    "duration": f"{int(a.spec['for_s'])}s",
                }
            }
        else:
            condition = {
                "condition_threshold": {
                    "filter": f'metric.type="workload.googleapis.com/{metric}"',
                    "comparison": "COMPARISON_LT" if a.kind == "ratio_below" else "COMPARISON_GT",
                    "threshold_value": float(
                        a.spec.get("above", a.spec.get("below", a.spec.get("factor", 0)))
                    ),
                    "duration": f"{int(a.spec.get('for_s', 0))}s",
                }
            }
        resources[name] = {
            "project": "${var.project_id}",
            "display_name": name,
            "combiner": "OR",
            "conditions": [{"display_name": name, **condition}],
            "notification_channels": f"${{var.{a.severity}_channels}}",
            "documentation": {
                "content": f"Runbook: docs/runbooks/{a.runbook}. Owner: {a.owner}.",
                "mime_type": "text/markdown",
            },
            "user_labels": {"severity": a.severity, "owner": a.owner.replace("-", "_")},
        }
    return {"resource": {"google_monitoring_alert_policy": resources}}
