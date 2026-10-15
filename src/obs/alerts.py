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
AVAILABILITY_TARGET = Fraction("0.999")  # slos.yaml api_availability (ARCH 99.9%)


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
            AVAILABILITY_TARGET,
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


SELECTOR_KEYS = ("state", "degraded", "failure_class", "outcome")


def promql_name(metric: str) -> str:
    """A ``workload.googleapis.com`` metric as Cloud Monitoring's PromQL names it."""
    return f"workload_googleapis_com:{metric}"


def _selector(spec: Mapping[str, Any], extra: str = "") -> str:
    parts = [f'{k}="{spec[k]}"' for k in SELECTOR_KEYS if k in spec]
    if "outcome_not" in spec:
        parts.append(f'outcome!~"{"|".join(str(v) for v in spec["outcome_not"])}"')
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def promql(alert: Alert) -> str:
    """The alert's condition as PromQL, the same semantics as ``fires`` (counters by their
    increase over the window, never their running total)."""
    s = alert.spec
    if alert.kind == "burn":
        m = promql_name("api_requests_total")
        bar = float(Fraction(str(s["factor"])) * (1 - AVAILABILITY_TARGET))

        def ratio(w: str) -> str:
            bad = f'sum(rate({m}{{code_class=~"5xx|timeout"}}[{w}]))'
            return f"({bad} / sum(rate({m}[{w}]))) > {bar:g}"

        volume = f"sum(increase({m}[{s['window']}])) >= {int(s.get('min_events', 100))}"
        return f"{ratio(str(s['window']))} and {ratio(str(s['short']))} and {volume}"
    m = promql_name(str(s["metric"]))
    if alert.kind == "any":
        return f"sum(increase({m}{_selector(s)}[10m])) > 0"
    if alert.kind == "rate_above":
        return f"sum(increase({m}{_selector(s)}[{int(s['window_s'])}s])) > {float(s['above']):g}"
    if alert.kind == "ratio_below":
        return (
            f"avg(avg_over_time({m}{_selector(s)}[{int(s['window_s'])}s])) < {float(s['below']):g}"
        )
    if alert.kind == "threshold":
        if str(s["metric"]).endswith("_ms"):  # a latency histogram: its p99
            return (
                f"histogram_quantile(0.99, sum by (le) (rate({m}_bucket{_selector(s)}[5m])))"
                f" > {float(s['above']):g}"
            )
        return f"max({m}{_selector(s)}) > {float(s['above']):g}"
    return f"absent_over_time({m}[{int(s['for_s'])}s])"  # absent


def terraform(alerts: Mapping[str, Alert]) -> dict[str, Any]:
    """Terraform JSON: one ``google_monitoring_alert_policy`` per alert with a PromQL condition
    (channels and project are variables; nothing is applied). An alert with ``paused`` (its
    metric's emitter is not deployed) is left out so it cannot page on silence."""
    resources: dict[str, Any] = {}
    for name in sorted(alerts):
        a = alerts[name]
        if a.spec.get("paused"):
            continue
        hold = int(a.spec["for_s"]) if a.kind == "threshold" else 0
        condition = {
            "condition_prometheus_query_language": {
                "query": promql(a),
                "duration": f"{hold}s",
                "evaluation_interval": "60s",
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
