#!/usr/bin/env python3
"""Reference implementation of the evidence and grading engine (Architecture v2 section 6.2).

This is the behaviour the production module `src/agents/grading.py` must reproduce. Pure functions, no I/O,
no model calls. Weights and half-lives come from assets/grading.yaml (starting values, tuned on the golden set).

    python scripts/grading_reference.py --selftest     # runs the worked examples and edge cases
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

DEFAULT_WEIGHTS = {"T1": 0.90, "T2": 0.75, "T3": 0.70, "T4": 0.40}
DEFAULT_HALF_LIFE = {"hiring": 21.0, "technology": 90.0, "leadership": 180.0, "funding": 365.0}


def load_config():
    """Read weights and half-lives from assets/grading.yaml when PyYAML is present, else use defaults."""
    p = Path(__file__).resolve().parent.parent / "assets" / "grading.yaml"
    if yaml is None or not p.exists():
        return DEFAULT_WEIGHTS, DEFAULT_HALF_LIFE
    cfg = yaml.safe_load(p.read_text())["engine"]
    w = {k.split("_")[0]: v["weight"] for k, v in cfg["source_tiers"].items()}
    return w, {k: float(v) for k, v in cfg["half_life_days"].items()}


@dataclass(frozen=True)
class Evidence:
    tier: str              # T1..T4
    lineage_group: str     # sources with a common upstream origin share a group
    retrieved_at: datetime # when the source was observed or published (age is measured from here)
    value: str             # NORMALISED value (normalise in code before grading)


def recency(age_days: float, half_life_days: float) -> float:
    """r = 0.5 ** (age / half_life); a future or zero age counts as fresh."""
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def group_strengths(evidence: list[Evidence], attr_class: str, now: datetime, weights=None, half_lives=None):
    """Return {lineage_group: (strength, best_tier)}. Within a group take the MAX of w*r, never the sum."""
    weights = weights or DEFAULT_WEIGHTS
    half_lives = half_lives or DEFAULT_HALF_LIFE
    hl = half_lives[attr_class]
    out: dict[str, tuple[float, str]] = {}
    for e in evidence:
        age = (now - e.retrieved_at).total_seconds() / 86400.0
        s = weights[e.tier] * recency(age, hl)
        if e.lineage_group not in out or s > out[e.lineage_group][0]:
            out[e.lineage_group] = (s, e.tier)
    return out


def confidence(strengths: dict[str, tuple[float, str]]) -> float:
    """Noisy-OR across independent lineage groups: c = 1 - prod(1 - w_i * r_i)."""
    p = 1.0
    for s, _ in strengths.values():
        p *= (1.0 - s)
    return 1.0 - p


def grade(c: float, strengths: dict[str, tuple[float, str]]) -> str:
    """A needs c >= 0.85, >= 2 independent lineage groups, and at least one T1 or T2 group."""
    n_groups = len(strengths)
    has_primary_or_curated = any(t in ("T1", "T2") for _, t in strengths.values())
    if c >= 0.85 and n_groups >= 2 and has_primary_or_curated:
        return "A"
    if c >= 0.70:
        return "B"
    if c >= 0.50:
        return "C"
    return "D"


def grade_attribute(evidence: list[Evidence], attr_class: str, now: datetime | None = None,
                    weights=None, half_lives=None) -> dict:
    """Grade one attribute. Distinct normalised values are scored separately; two values supported by
    different lineage groups that both exceed c = 0.50 make the attribute CONFLICTING (show both)."""
    now = now or datetime.now(timezone.utc)
    if not evidence:
        return {"status": "no_evidence"}          # the caller records Hypothesis (absence of evidence), not Lead
    by_value: dict[str, list[Evidence]] = {}
    for e in evidence:
        by_value.setdefault(e.value, []).append(e)
    scored = {}
    for v, ev in by_value.items():
        st = group_strengths(ev, attr_class, now, weights, half_lives)
        c = confidence(st)
        scored[v] = {"confidence": round(c, 4), "grade": grade(c, st), "groups": sorted(st)}
    strong = [v for v, d in scored.items() if d["confidence"] > 0.50]
    if len(strong) >= 2:
        groups = [set(scored[v]["groups"]) for v in strong]
        if any(a.isdisjoint(b) for i, a in enumerate(groups) for b in groups[i + 1:]):
            return {"status": "conflicting", "values": {v: scored[v] for v in strong}}
    best = max(scored, key=lambda v: scored[v]["confidence"])
    return {"status": "graded", "value": best, **scored[best]}


# --------------------------------------------------------------------------- self-test
def _selftest() -> int:
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + name)
        ok &= bool(cond)

    # ARCH 6.2 worked example 1: fresh T1 + fresh T2, independent lineage -> c = 0.975, grade A
    r = grade_attribute([Evidence("T1", "site", t0, "jane doe"), Evidence("T2", "curated", t0, "jane doe")],
                        "leadership", t0)
    check("worked example 1: c = 0.975 and grade A", abs(r["confidence"] - 0.975) < 1e-9 and r["grade"] == "A")

    # ARCH 6.2 worked example 2: two aggregators copying one upstream = ONE group, c = 0.40 at best, grade D
    r = grade_attribute([Evidence("T4", "upstream-x", t0, "jane doe"), Evidence("T4", "upstream-x", t0, "jane doe")],
                        "leadership", t0)
    check("worked example 2: one lineage group, c = 0.40, grade D", abs(r["confidence"] - 0.40) < 1e-9 and r["grade"] == "D")

    # recency: after exactly one half-life a T1 hiring signal is worth 0.45
    r = grade_attribute([Evidence("T1", "site", datetime(2026, 9, 10, tzinfo=timezone.utc), "x")], "hiring", t0)
    check("21-day-old T1 hiring signal: c = 0.45", abs(r["confidence"] - 0.45) < 1e-9)

    # a single fresh T1 source can reach B but never A (needs two independent groups)
    r = grade_attribute([Evidence("T1", "site", t0, "x")], "funding", t0)
    check("single fresh T1 source: c = 0.90 but grade B, not A", r["grade"] == "B" and abs(r["confidence"] - 0.90) < 1e-9)

    # two independent T3 groups reach c > 0.85 but have no T1/T2 group -> not A
    r = grade_attribute([Evidence("T3", "n1", t0, "x"), Evidence("T3", "n2", t0, "x")], "funding", t0)
    check("two fresh T3 groups: c = 0.91, but no T1/T2 group so grade B", r["grade"] == "B" and abs(r["confidence"] - 0.91) < 1e-9)

    # conflict: two independent groups assert different values, both c > 0.50
    r = grade_attribute([Evidence("T1", "site", t0, "jane doe"), Evidence("T2", "curated", t0, "john roe")],
                        "leadership", t0)
    check("different values from independent groups, both > 0.50: conflicting", r["status"] == "conflicting")

    # no conflict when the second value is weak
    r = grade_attribute([Evidence("T1", "site", t0, "jane doe"), Evidence("T4", "agg", t0, "john roe")],
                        "leadership", t0)
    check("weak competing value (0.40) does not conflict", r["status"] == "graded" and r["value"] == "jane doe")

    # inside one lineage group take the max, never the sum
    r = grade_attribute([Evidence("T2", "g", t0, "x"), Evidence("T4", "g", t0, "x")], "technology", t0)
    check("same group, T2 + T4: max (0.75) not sum", abs(r["confidence"] - 0.75) < 1e-9)

    check("no evidence is reported as no_evidence (caller records Hypothesis)", grade_attribute([], "hiring", t0)["status"] == "no_evidence")

    w, h = load_config()
    check("config loads: weights T1..T4 and four half-lives", set(w) >= {"T1", "T2", "T3", "T4"} and len(h) == 4)
    print("selftest OK" if ok else "selftest FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else print(__doc__) or 0)
