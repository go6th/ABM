"""The virtual-clock simulation (D-44), single flight (P8) and the no-model static guard."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from parallel.config import load_parallel_config
from parallel.simulate import Scenario, critical_path_bound, simulate
from parallel.singleflight import InProcessSingleFlight

CFG = load_parallel_config()
ROUTES = ["people_by_title", "profile_enrichment", "scrape", "structured_signals", "web_search"]
STORED = {
    "senior_people": [f"p{i}" for i in range(6)],
    "top_people": [f"p{i}" for i in range(4)],
    "priority_a_people": [f"p{i}" for i in range(2)],
}
ROOT = Path(__file__).resolve().parents[2]


def scenario(runs: int, limit: int, latency: dict[str, float], **kw: object) -> Scenario:
    return Scenario(
        runs=runs,
        latency_s=latency,
        stored=STORED,
        provider_limits={r: limit for r in ROUTES},
        governor_ceilings={r: limit for r in ROUTES},
        **kw,  # type: ignore[arg-type]
    )


@settings(max_examples=15, deadline=None)
@given(st.dictionaries(st.sampled_from(sorted(CFG.unit_types)), st.integers(1, 120), min_size=3))
def test_simulated_makespan_equals_the_critical_path_when_unconstrained(lat: dict) -> None:
    latency = {t: float(lat.get(t, 10)) for t in CFG.unit_types}
    report = simulate(CFG, scenario(1, 10_000, latency, max_in_flight=10_000))
    assert report.unfinished == 0 and report.violations == 0
    assert report.makespans_s[0] == pytest.approx(critical_path_bound(CFG, scenario(1, 1, latency)))


def test_makespan_degrades_predictably_as_limits_tighten() -> None:
    latency = {t: 30.0 for t in CFG.unit_types}
    spans = [
        simulate(CFG, scenario(1, lim, latency, max_in_flight=1000)).makespans_s[0]
        for lim in (100, 4, 2, 1)
    ]
    assert spans == sorted(spans)  # never faster with fewer slots
    assert spans[0] == critical_path_bound(CFG, scenario(1, 1, latency))


def test_simulation_has_no_limit_violation_with_50_runs() -> None:
    latency = {t: 20.0 for t in CFG.unit_types}
    report = simulate(CFG, scenario(50, 3, latency))
    assert report.violations == 0
    assert all(st.peak <= 3 for st in report.providers.values())
    assert report.unfinished == 0
    text = report.markdown()
    assert "This is a simulation" in text and "does not show that the 4 hour target is met" in text


def test_tight_deadline_blocks_units_in_the_simulation_report() -> None:
    latency = {t: 100.0 for t in CFG.unit_types}
    report = simulate(CFG, scenario(1, 1, latency, deadline_s=300.0))
    assert report.blocked.get("deadline", 0) > 0 and report.unfinished == 0


@pytest.mark.asyncio
async def test_single_flight_runs_concurrent_identical_calls_once() -> None:
    sf = InProcessSingleFlight()
    calls = {"n": 0}

    async def pull() -> str:
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return "payload-ref"

    results = await asyncio.gather(*(sf.do("run:key", pull) for _ in range(10)))
    assert results == ["payload-ref"] * 10 and calls["n"] == 1 and sf.shared == 9
    assert await sf.do("run:key", pull) == "payload-ref" and calls["n"] == 2  # later: runs again

    async def broken() -> str:
        await asyncio.sleep(0.01)
        raise RuntimeError("provider failed")

    outcomes = await asyncio.gather(
        *(sf.do("k2", broken) for _ in range(3)), return_exceptions=True
    )
    assert all(isinstance(o, RuntimeError) for o in outcomes)


LLM_IMPORT = re.compile(
    r"^\s*(from|import)\s+(anthropic|openai|google\.genai|vertexai|"
    r"langchain\w*|account_intelligence\.platform\.llm)\b",
    re.M,
)


def test_no_model_client_is_imported_by_parallel_or_spend() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for top in ("src/parallel", "src/spend")
        for p in (ROOT / top).rglob("*.py")
        if LLM_IMPORT.search(p.read_text())
    ]
    assert offenders == []


def test_no_vendor_name_in_the_parallel_package_or_its_config_keys() -> None:
    import yaml

    routing = yaml.safe_load((ROOT / "config/routing.yaml").read_text())
    names = [
        str(v["display_name"]).lower()
        for v in routing["brokers"].values()
        if v.get("paid") and len(str(v["display_name"])) > 3
    ]
    assert names  # the guard reads the real vendor list (never hard-coded here)
    texts = [p.read_text().lower() for p in (ROOT / "src/parallel").rglob("*.py")]
    texts.append((ROOT / "config/parallel.yaml").read_text().lower())
    for text in texts:
        assert not any(n in text for n in names)
