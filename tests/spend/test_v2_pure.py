"""Credit manager v2, pure rules: soft and hard limits (M1), the funding mode (M3), the pause
request arithmetic (M4), the exhaustion ladder (M7), the notification allow-list (M10), the v2
config and the live tenant view (M2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from spend.config import build_v2, load_v2_config
from spend.errors import ConfigError
from spend.exhaustion import Alternate, alternate_allowed, decide, file_spec, spec_hash
from spend.limits import LimitInputs, Mode, crossed, mode, soft_threshold
from spend.live import TENANT_KEYS, Snapshot
from spend.notify import TEMPLATES, NotifyError, check, render
from spend.pause import BlockedUnit, compute_request

ROOT = Path(__file__).resolve().parents[2]
CFG = load_v2_config()


@given(st.integers(0, 10**12), st.integers(1, 99))
def test_soft_is_always_below_hard_and_integer(cap: int, pct: int) -> None:
    soft = soft_threshold(cap, pct)
    assert isinstance(soft, int) and 0 <= soft <= cap
    if cap > 0:
        assert soft < cap
    assert soft == cap * pct // 100


def test_soft_threshold_refuses_bad_input() -> None:
    with pytest.raises(ValueError):
        soft_threshold(100, 100)
    with pytest.raises(ValueError):
        soft_threshold(100, 0)
    with pytest.raises(ValueError):
        soft_threshold(-1, 50)


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        (LimitInputs(1_000_000, 1, 100, 0), Mode.NORMAL),
        (LimitInputs(1_000_000, 1, 700_000, 100_000), Mode.PRIORITY_ONLY),  # 80% incl. held
        (LimitInputs(1_000_000, 1, 0, 0, pause_open=True), Mode.PAUSED),
        (LimitInputs(1_000_000, 1, 0, 0, pause_open=True, stopped=True), Mode.STOPPED),
        (LimitInputs(None, None, 5, 5), Mode.NORMAL),  # no cap: K2 owns it, nothing to cross
    ],
)
def test_funding_mode_follows_the_pause_lifecycle(inputs: LimitInputs, expected: Mode) -> None:
    assert mode(inputs, 80) is expected
    assert crossed(LimitInputs(1_000_000, 1, 799_999, 0), 80) is False
    assert crossed(LimitInputs(1_000_000, 1, 799_999, 1), 80) is True


def test_pause_request_is_computed_in_code_with_bases() -> None:
    req = compute_request(
        cap_version=2,
        cap_micros=5_000_000,
        committed_micros=4_900_000,
        held_micros=50_000,
        blocked=[
            BlockedUnit("body|jobs", 300_000, ("job_board_by_lane",)),
            BlockedUnit("body|tech", None, ("technology_detections",)),
        ],
        step_micros=50_000,
        unknown_hold_micros=250_000,
        ceiling_micros=None,
    )
    assert req.needed_micros == 4_900_000 + 50_000 + 300_000 + 250_000
    assert req.shortfall_micros == req.needed_micros - 5_000_000
    assert req.suggested_cap_micros == 5_500_000  # rounded up to the 0.05 step
    assert req.blocked_unknown_units == 1 and req.room
    assert req.rows_at_risk == ("job_board_by_lane", "technology_detections")
    for field in (
        "committed_micros",
        "held_micros",
        "needed_micros",
        "suggested_cap_micros",
        "shortfall_micros",
        "unknown_hold_micros",
        "blocked_known_micros",
    ):
        assert req.bases[field]
    assert len(req.content_hash()) == 64


@given(
    st.integers(1, 10**8),
    st.integers(0, 10**8),
    st.integers(0, 10**7),
    st.lists(st.one_of(st.none(), st.integers(0, 10**7)), max_size=5),
    st.one_of(st.none(), st.integers(1, 2 * 10**8)),
)
def test_pause_suggestion_is_above_the_cap_or_reports_no_room(
    cap: int, committed: int, held: int, ests: list[int | None], ceiling: int | None
) -> None:
    req = compute_request(
        1,
        cap,
        committed,
        held,
        [BlockedUnit(f"u{i}", e, ()) for i, e in enumerate(ests)],
        50_000,
        250_000,
        ceiling,
    )
    assert req.shortfall_micros >= 0
    if ceiling is not None:
        assert req.suggested_cap_micros <= ceiling
    if req.room:
        assert req.suggested_cap_micros > cap
    else:
        assert ceiling is not None and ceiling <= cap


ALTS = st.lists(
    st.builds(
        Alternate,
        route=st.sampled_from(["a", "b", "c"]),
        provider_code=st.just("p_x"),
        operation=st.just("op"),
        translation=st.booleans(),
        circuit_open=st.booleans(),
        exhausted=st.booleans(),
    ),
    max_size=4,
)


@given(st.booleans(), ALTS, st.booleans())
def test_ladder_order_is_prepulled_then_alternate_then_upload(
    prepulled: bool, alternates: list[Alternate], allowed: bool
) -> None:
    rung = decide(prepulled, alternates, allowed)
    usable = [a for a in alternates if a.translation and not a.circuit_open and not a.exhausted]
    if prepulled:
        assert rung.action == "use_prepulled"
    elif allowed and usable:
        assert rung.action == "use_alternate" and rung.alternate == usable[0]
    else:
        assert rung.action == "block_and_request_upload" and rung.alternate is None
    assert decide(prepulled, alternates, allowed) == rung


def test_an_alternate_route_that_is_itself_exhausted_is_skipped() -> None:
    dead = Alternate("a", "p_y", "op", True, False, exhausted=True)
    assert decide(False, [dead], True).action == "block_and_request_upload"


def test_tenant_policy_decides_the_alternate_and_the_default_is_d47() -> None:
    assert alternate_allowed(None, True) is True
    assert alternate_allowed({"allow_alternate_on_exhaustion": False}, True) is False
    assert alternate_allowed({"allow_fallback_on_exhaustion": False}, True) is False  # legacy key
    assert alternate_allowed({"allow_alternate_on_exhaustion": "yes"}, False) is False
    assert CFG.allow_alternate_on_exhaustion is True


def test_upload_request_carries_the_exact_file_spec() -> None:
    pending = [
        {"args_hash": "b" * 64, "args": {"domain": "acme.example"}},
        {"args_hash": "a" * 64, "args": {"domain": "acme.example", "page": 2}},
    ]
    spec = file_spec("p_signals", "news_events", pending, "acme.example", CFG)
    assert [c["args_hash"] for c in spec["pending_calls"]] == ["a" * 64, "b" * 64]
    assert spec["max_bytes"] == CFG.upload_max_bytes
    assert spec["attestations_required"] == ["account", "pull_date"]
    assert spec_hash(spec) == spec_hash(
        file_spec("p_signals", "news_events", list(reversed(pending)), "acme.example", CFG)
    )


def test_notifications_use_templates_and_allow_listed_parameters_only() -> None:
    assert check("run_waiting_for_approval", "tenant", {"run_id": "r1", "request_id": "q1"})
    with pytest.raises(NotifyError):
        check("run_waiting_for_approval", "operator", {"run_id": "r1", "request_id": "q1"})
    with pytest.raises(NotifyError):
        check("run_waiting_for_approval", "tenant", {"run_id": "r1"})
    with pytest.raises(NotifyError):
        check(
            "run_waiting_for_approval",
            "tenant",
            {"run_id": "r1", "request_id": "q1 <script>alert(1)</script>"},
        )
    with pytest.raises(NotifyError):
        check("no_such_template", "tenant", {})
    with pytest.raises(NotifyError):
        check("spend_soft_limit", "operator", {"run_id": "r", "cap_version": True})


def test_tenant_notifications_pass_the_client_output_scan(tmp_path: Path) -> None:
    import subprocess
    import sys

    routing = yaml.safe_load((ROOT / "config/routing.yaml").read_text())
    vendors = [str(v["display_name"]).lower() for v in routing["brokers"].values()]
    for name, tpl in TEMPLATES.items():
        if tpl.audience != "tenant":
            continue
        params = {p: "00000000-0000-4000-8000-000000000001" for p in tpl.params}
        subject, body = render(name, params)
        text = f"{subject}\n{body}"
        assert "p_" not in text and "$" not in text and "credit" not in text.lower()
        assert not any(v in text.lower() for v in vendors if len(v) > 3 and v != "parallel")
        (tmp_path / f"{name}.txt").write_text(text)
    scanner = ROOT / ".claude/skills/account-intelligence-platform/scripts/scan_client_output.py"
    result = subprocess.run(
        [sys.executable, str(scanner), str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tenant_live_view_is_allow_listed() -> None:
    snap = Snapshot(
        "r1",
        "2026-01-01T00:00:00+00:00",
        1,
        5_000_000,
        4_000_000,
        4_100_000,
        50_000,
        850_000,
        Mode.PRIORITY_ONLY,
        (
            {
                "provider_code": "p_search",
                "category": "pages_read",
                "done_calls": 3,
                "charged_micros": 15_000,
            },
        ),
        {"label": "estimate", "micros": 1, "base": "x"},
    )
    view = snap.tenant_view({"pages_read": 3})
    assert set(view) == TENANT_KEYS and view["status"] == "near_limit"
    blob = repr(view)
    assert "p_search" not in blob and "micros" not in blob and "4100000" not in blob


def test_v2_config_is_sourced_and_validated() -> None:
    data = yaml.safe_load((ROOT / "config/spend.yaml").read_text())
    for key, entry in data["v2"].items():
        if isinstance(entry, dict):
            assert {"source", "status"} & set(entry), key
    bad = yaml.safe_load((ROOT / "config/spend.yaml").read_text())
    bad["v2"]["soft_limit_pct"] = {"value": 100, "status": "x"}
    with pytest.raises(ConfigError, match="below 100"):
        build_v2(bad)
    bad = yaml.safe_load((ROOT / "config/spend.yaml").read_text())
    bad["v2"]["allow_alternate_on_exhaustion"] = {"value": "yes", "status": "x"}
    with pytest.raises(ConfigError):
        build_v2(bad)
    bad = yaml.safe_load((ROOT / "config/spend.yaml").read_text())
    bad["v2"]["tenant_ceiling_usd"] = {"value": "abc", "status": "x"}
    with pytest.raises(ConfigError):
        build_v2(bad)
    with pytest.raises(ConfigError):
        build_v2({"v2": {"value": 1}})
