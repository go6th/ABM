"""ObsGuard on pure code: the contract (catalogs, code, alerts, dashboards, runbooks), the
scrubber, trace identity, SLO and burn arithmetic, the alert simulator, stuck classification, cost
arithmetic, the release verdict, heartbeats."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from obs import alerts, context, costs, health, scrub, slo, stuck, verdict
from obs.catalog import FORBIDDEN, build, load_catalog
from obs.errors import CatalogInvalid

ROOT = Path(__file__).resolve().parents[2]
TEL = ROOT / "config/telemetry"
CAT = load_catalog()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def docs() -> tuple[dict, dict, dict]:
    return tuple(
        yaml.safe_load((TEL / f).read_text())  # type: ignore[return-value]
        for f in ("metrics.yaml", "spans.yaml", "logs.yaml")
    )


def test_catalogs_validate_and_refuse_forbidden_labels() -> None:
    assert {"connector_calls", "llm_calls_total", "slo_burn_rate"} <= set(CAT.metrics)
    m, s, lg = docs()
    m["metrics"]["connector_calls"]["labels"] = ["provider_code", "tenant_id"]
    with pytest.raises(CatalogInvalid, match="forbidden"):
        build(m, s, lg, "x")
    m, s, lg = docs()
    m["metrics"]["connector_calls"]["series_bound"] = 10
    with pytest.raises(CatalogInvalid, match="exceed"):
        build(m, s, lg, "x")
    m, s, lg = docs()
    s["spans"]["run"]["attributes"].append("url")
    with pytest.raises(CatalogInvalid, match="forbidden"):
        build(m, s, lg, "x")
    m, s, lg = docs()
    lg["fields"].append("prompt")
    with pytest.raises(CatalogInvalid):
        build(m, s, lg, "x")
    assert "run_id" in FORBIDDEN and "tenant_id" in FORBIDDEN


@pytest.mark.parametrize("script", ["check_telemetry.py", "check_runbooks.py"])
def test_code_and_runbooks_follow_the_contract(script: str) -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout


def test_generated_terraform_is_current_and_deterministic() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/gen_observability.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout
    body = (ROOT / "infra/modules/observability/generated_alerts.tf.json").read_text()
    assert "@" not in body.replace("${", "") or "@" not in body  # no email or real id
    assert "var.project_id" in body


def test_dashboards_reference_catalog_metrics_only() -> None:
    from obs import dashboards

    out = dashboards.render(CAT, TEL)
    assert set(out["resource"]["google_monitoring_dashboard"]) >= {"failures", "cost"}


@given(
    st.dictionaries(
        st.text(max_size=12),
        st.one_of(
            st.text(max_size=300), st.integers(), st.none(), st.booleans(), st.lists(st.integers())
        ),
    )
)
def test_clean_never_passes_unknown_keys_or_bad_shapes(attrs: dict) -> None:
    allowed = frozenset({"provider_code", "outcome", "attempt"})
    kept, dropped = scrub.clean(attrs, allowed, 200)
    assert set(kept) <= allowed and dropped == len(attrs) - len(kept)
    for v in kept.values():
        assert scrub.value_ok(v, 200)
    assert scrub.clean(kept, allowed, 200) == (kept, 0)  # idempotent


@pytest.mark.parametrize(
    "bad",
    [
        "https://x.example/a?token=1",
        "https://user:pw@x.example/",
        "jane@acme.example",
        "sk-abcdefghijklmnopqrstuvwx",
        "Bearer abcdefghijklmnopqrstu",
        "line\x00break",
        "x" * 201,
        "CANARY-7f3a9e",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
    ],
)
def test_shape_filter_refuses_content_and_secrets(bad: str) -> None:
    assert not scrub.value_ok(bad, 200)


def test_exceptions_keep_class_code_and_hash_only() -> None:
    out = scrub.scrub_exception(ValueError("the page text CANARY-7f3a9e and a secret"))
    assert out["error_class"] == "ValueError" and "CANARY" not in str(out)
    assert len(out["message_hash"]) == 16


@given(st.uuids(), st.uuids())
def test_trace_ids_are_total_and_injective(a: UUID, b: UUID) -> None:
    assert context.trace_id_for(a) == a.int or a.int == 0
    if a != b and a.int and b.int:
        assert context.trace_id_for(a) != context.trace_id_for(b)
    assert context.trace_id_for(str(a)) == context.trace_id_for(a)
    tp = context.encode(context.trace_id_for(a), context.span_id_for(a, "stage", 1))
    assert context.decode(tp) == (context.trace_id_for(a), context.span_id_for(a, "stage", 1), True)
    assert context.span_id_for(a, "x") == context.span_id_for(a, "x") != 0
    assert context.decode("garbage") is None and context.trace_id_for("not-a-uuid") > 0


@given(st.integers(0, 1000), st.integers(0, 1000))
def test_burn_math_is_exact_and_monotone(bad: int, extra: int) -> None:
    total = bad + extra
    target = Fraction(999, 1000)
    b = slo.burn(bad, total, target)
    if total == 0:
        assert b is None  # NoData, never 100% good
        return
    assert b == Fraction(bad, total) / Fraction(1, 1000)
    assert b == slo.burn(bad * 3, total * 3, target)  # scale invariant
    if bad < total:
        more = slo.burn(bad + 1, total, target)
        assert more is not None and more > b  # monotone in bad events
    assert slo.burn(0, total, target) == 0


def test_multiwindow_page_needs_both_windows_and_volume() -> None:
    t, f = Fraction(999, 1000), Fraction("14.4")
    assert slo.multiwindow_page(slo.Window(20, 1000), slo.Window(5, 100), t, f, 100)
    assert not slo.multiwindow_page(slo.Window(20, 1000), slo.Window(0, 100), t, f, 100)
    assert not slo.multiwindow_page(slo.Window(1, 1), slo.Window(1, 1), t, f, 100)  # one request


def test_sli_oracles() -> None:
    runs = [
        slo.RunOutcome("COMPLETED", 100, 0),
        slo.RunOutcome("PARTIAL", 3000, 600),
        slo.RunOutcome("FAILED", 10, 0),
        slo.RunOutcome("CANCELED", 5, 0),
        slo.RunOutcome("COMPLETED", 5000, 0),
    ]
    assert slo.run_completion(runs, 2700) == (2, 4)  # human wait excluded, CANCELED excluded
    good, total, err = slo.latency_good_from_buckets([100, 250, 500, 1000], [5, 3, 2, 4, 1], 500)
    assert (good, total, err) == (10, 15, 0)
    good, total, err = slo.latency_good_from_buckets([100, 400, 800], [5, 3, 2, 1], 500)
    assert (good, total, err) == (8, 11, 2)  # the straddling bucket bounds the error
    assert slo.budget_remaining(0, 0, Fraction(999, 1000)) is None
    assert slo.budget_remaining(1, 1000, Fraction(999, 1000)) == 0


def scenarios() -> dict[str, list[alerts.Sample]]:
    req = "api_requests_total"
    outage = [alerts.Sample(t, req, 50, {"code_class": "5xx"}) for t in range(3300, 3600, 10)]
    outage += [alerts.Sample(t, req, 50, {"code_class": "2xx"}) for t in range(0, 3600, 10)]
    slow = [alerts.Sample(t, req, 7, {"code_class": "5xx"}) for t in range(0, 21600, 60)]
    slow += [alerts.Sample(t, req, 993, {"code_class": "2xx"}) for t in range(0, 21600, 60)]
    return {
        "api_availability_fast_burn": outage,
        "api_availability_slow_burn": slow,
        "api_latency_p99": [alerts.Sample(t, "api_latency_ms", 900) for t in range(0, 3600, 60)],
        "isolation_canary_leak": [alerts.Sample(10, "canary_hits_total", 1, {"sink": "logs"})],
        "isolation_probe_leak": [alerts.Sample(10, "isolation_probe_leaks_total", 1)],
        "rls_denied_spike": [
            alerts.Sample(t, "rls_denied_total", 1) for t in range(3400, 3600, 10)
        ],
        "cache_ratio_low": [
            alerts.Sample(t, "cache_read_ratio", 0.4, {"volume": "2000"})
            for t in range(0, 3600, 300)
        ],
        "stuck_queued": [
            alerts.Sample(t, "stuck_runs", 1, {"state": "QUEUED"}) for t in range(0, 3600, 60)
        ],
        "stuck_running": [
            alerts.Sample(t, "stuck_runs", 1, {"state": "RUNNING"}) for t in range(0, 3600, 60)
        ],
        "telemetry_down": [alerts.Sample(0, "heartbeat", 1)],
        "cost_drift": [alerts.Sample(5, "cost_drift_total", 1)],
        "unknown_cost": [alerts.Sample(3500, "unknown_cost_calls_total", 2)],
        "provider_failures": [
            alerts.Sample(t, "connector_calls", 1, {"outcome": "RateLimited"})
            for t in range(3000, 3600, 10)
        ],
        "model_degraded": [
            alerts.Sample(t, "llm_calls_total", 1, {"degraded": "true"})
            for t in range(3000, 3600, 10)
        ],
        "checkpoint_resume_failures": [
            alerts.Sample(3500, "failures_total", 1, {"failure_class": "resume"})
        ],
        "sso_signin_refusals": [
            alerts.Sample(t, "auth_signin_refusals_total", 1, {"outcome": "state_expired"})
            for t in range(3000, 3600, 20)
        ],
        "connector_access_refused": [
            alerts.Sample(t, "connector_access_refused_total", 1, {"outcome": "denied"})
            for t in range(3000, 3600, 30)
        ],
        "tenant_suspended": [alerts.Sample(3500, "tenant_suspensions_total", 1)],
        "tenant_deletion_requested": [alerts.Sample(3500, "tenant_deletion_requests_total", 1)],
        "telemetry_dropping": [
            alerts.Sample(t, "telemetry_dropped_total", 10) for t in range(3100, 3600, 10)
        ],
    }


def test_every_alert_fires_on_its_incident_and_stays_quiet_when_healthy() -> None:
    loaded = alerts.load(CAT, TEL, ROOT / "docs/runbooks")
    incidents = scenarios()
    assert set(loaded) == set(incidents)  # every alert has a firing scenario
    healthy = [
        alerts.Sample(t, "api_requests_total", 1000, {"code_class": "2xx"})
        for t in range(0, 21600, 60)
    ]
    healthy += [alerts.Sample(t, "heartbeat", 1) for t in range(0, 21600, 60)]
    healthy += [
        alerts.Sample(t, "cache_read_ratio", 0.9, {"volume": "2000"}) for t in range(0, 21600, 300)
    ]
    for name, alert in loaded.items():
        at = 21600 if name == "api_availability_slow_burn" else 3600
        assert alerts.fires(alert, incidents[name], at), name
        assert not alerts.fires(alert, healthy, 21600), name
    one_bad = [alerts.Sample(100, "api_requests_total", 1, {"code_class": "5xx"})]
    assert not alerts.fires(loaded["api_availability_fast_burn"], one_bad, 200)  # min events
    low_volume = [alerts.Sample(10, "cache_read_ratio", 0.1, {"volume": "5"})]
    assert not alerts.fires(loaded["cache_ratio_low"], low_volume, 100)
    # the cache invalidation canary fires within one hour (ARCH chaos)
    drop = [alerts.Sample(t, "cache_read_ratio", 0.2, {"volume": "20000"}) for t in (600, 1200)]
    assert alerts.fires(loaded["cache_ratio_low"], drop, 1200)


def test_alerts_without_runbook_or_catalog_metric_are_refused(tmp_path: Path) -> None:
    for f in ("slos.yaml", "severity.yaml", "dashboards.yaml"):
        (tmp_path / f).write_text((TEL / f).read_text())
    doc = yaml.safe_load((TEL / "alerts.yaml").read_text())
    doc["alerts"]["cost_drift"]["runbook"] = "missing.md"
    (tmp_path / "alerts.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(CatalogInvalid, match="runbook"):
        alerts.load(CAT, tmp_path, ROOT / "docs/runbooks")
    doc["alerts"]["cost_drift"]["runbook"] = "cost-drift.md"
    doc["alerts"]["cost_drift"]["metric"] = "made_up_metric"
    (tmp_path / "alerts.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(CatalogInvalid, match="catalog"):
        alerts.load(CAT, tmp_path, ROOT / "docs/runbooks")


def test_stuck_classification_never_flags_a_paused_or_busy_run() -> None:
    now = T0 + timedelta(hours=1)

    def view(
        status: str,
        created_ago: float,
        active_ago: float | None = None,
        deadline_in: float | None = None,
    ) -> stuck.RunView:
        return stuck.RunView(
            "r",
            status,
            now - timedelta(seconds=created_ago),
            None if active_ago is None else now - timedelta(seconds=active_ago),
            None if deadline_in is None else now + timedelta(seconds=deadline_in),
        )

    assert stuck.classify(view("QUEUED", 61), now, 960) == "queued_too_long"
    assert stuck.classify(view("QUEUED", 59), now, 960) is None
    assert stuck.classify(view("RUNNING", 3000, 1000), now, 960) == "no_activity"
    assert stuck.classify(view("RUNNING", 3000, 200), now, 960) is None  # a long, live slice
    assert stuck.classify(view("AWAITING_APPROVAL", 9000, None, 600), now, 960) is None
    assert (
        stuck.classify(view("AWAITING_APPROVAL", 9000, None, -1), now, 960)
        == "approval_past_deadline"
    )
    assert stuck.age_bucket(30) == "lt_5m" and stuck.age_bucket(90000) == "gt_24h"
    assert stuck.quiet_seconds(240, 4) == 960


def test_cost_rollup_arithmetic_is_exact() -> None:
    rows = [
        costs.LedgerRow("r", "p_a", "need", "documented", Decimal("0.010000")),
        costs.LedgerRow("r", "p_a", "need", "documented", Decimal("0.005001")),
        costs.LedgerRow("r", "p_a", "need", "documented", Decimal("0.5"), replayed=True),
        costs.LedgerRow("r", "p_b", None, "prepulled", Decimal("0")),
        costs.LedgerRow("r", "p_c", None, "unknown", None),
    ]
    lines = {ln.provider_code: ln for ln in costs.rollup_lines(rows)}
    assert lines["p_a"].cost_micros == 15001 and lines["p_a"].calls == 3  # the replay adds zero
    assert lines["p_b"].cost_micros == 0  # a pre-pulled file adds zero
    assert lines["p_c"].unknown_cost_calls == 1 and lines["p_c"].cost_micros == 0  # never zero:
    # counted as unknown, not as a priced call
    assert costs.rollup_lines(list(reversed(rows))) == costs.rollup_lines(rows)


def test_release_verdict_outcomes() -> None:
    assert verdict.verdict(verdict.Counts(1, 1000), None, 100, 0.01) == "no_baseline"
    assert (
        verdict.verdict(verdict.Counts(1, 10), verdict.Counts(1, 1000), 100, 0.01) == "low_sample"
    )
    assert (
        verdict.verdict(verdict.Counts(50, 1000), verdict.Counts(1, 1000), 100, 0.01)
        == "regression"
    )
    assert verdict.verdict(verdict.Counts(2, 1000), verdict.Counts(1, 1000), 100, 0.01) == "promote"


def test_silence_is_an_alert() -> None:
    now = T0 + timedelta(minutes=30)
    beats = {"api": now - timedelta(seconds=10), "worker": now - timedelta(minutes=20)}
    assert health.silent(beats, ["api", "worker", "relay"], now, 600) == ["relay", "worker"]
    assert health.watchdog({"traces": 500, "logs": 3}, {"traces": 100}, 100) == ["traces"]
