"""Property tests (spec 9.5) and static guarantees (J1, J13, J15, config provenance)."""

from __future__ import annotations

import ast
import inspect
import random
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import jobs.counts as counts_module
from jobs.config import load_jobs_config
from jobs.counts import compute_counts
from jobs.models import Classification, PlatformSentence, Posting, RawPosting
from jobs.window import classify_window, window_start

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ["build", "admin_owner", "working_tool", "user", "unclassified"]


@st.composite
def postings(draw: st.DrawFn) -> list[Posting]:
    n = draw(st.integers(0, 15))
    out = []
    for i in range(n):
        platforms = tuple(sorted(draw(st.sets(st.sampled_from(["SAP", "Salesforce"]), max_size=2))))
        title_only = () if platforms else tuple(draw(st.sets(st.just("SAP"), max_size=1)))
        hostile = draw(st.booleans())
        read = draw(st.booleans()) and not hostile and bool(platforms or title_only)
        label = draw(st.sampled_from(CLASSES)) if (read and platforms) else "unclassified"
        sentences = (
            tuple(
                PlatformSentence(platform=p, text=p, start=0, end=1, sha256="x") for p in platforms
            )
            if read
            else ()
        )
        out.append(
            Posting(
                posting_key=f"id:{i}",
                title="t",
                url=f"https://example.org/{i}",
                urls=(f"https://example.org/{i}",),
                provider_code="p_signals",
                raw_ref="r",
                retrieved_at="2026-01-01",
                status=draw(st.sampled_from(["open", "closed", "unknown"])),
                window_bucket=draw(st.sampled_from(["in_window", "out_of_window", "undated"])),
                platforms=platforms,
                title_platforms=title_only,
                read=read,
                full_text_read=read,
                hostile=hostile,
                sentences=sentences,
                classification=Classification(label=label, classified_by="rule", reason="x"),
            )
        )
    return out


def _raw(n: int) -> list[RawPosting]:
    return [
        RawPosting(title="t", url="https://e.org", provider_code="p", raw_ref="r", retrieved_at="x")
        for _ in range(n)
    ]


@given(postings(), st.integers(0, 5))
def test_count_invariants_hold_for_any_posting_set(rows: list[Posting], extra: int) -> None:
    c = compute_counts(_raw(len(rows) + extra), rows, ["SAP", "Salesforce"], {})
    d = c.distinct
    for group in (d.matched_status, d.read_status):
        assert group.open + group.closed + group.unknown_status == group.total
    assert sum(d.by_class.values()) == d.read
    assert d.read <= d.matched <= c.in_window <= c.deduped <= c.pulled
    for block in c.per_platform.values():
        assert block.matched <= d.matched and block.read <= d.read  # type: ignore[union-attr]


@given(postings())
def test_counts_do_not_depend_on_input_order(rows: list[Posting]) -> None:
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    assert compute_counts(_raw(len(rows)), rows, ["SAP"], {}) == compute_counts(
        _raw(len(rows)), shuffled, ["SAP"], {}
    )


@given(st.dates(date(2001, 1, 1), date(2099, 1, 1)), st.integers(1, 400))
def test_window_is_monotonic_in_as_of(as_of: date, days: int) -> None:
    later = as_of + timedelta(days=days)
    assert window_start(as_of) <= window_start(later)
    assert classify_window(window_start(as_of), as_of) == "in_window"
    assert classify_window(window_start(as_of) - timedelta(days=1), as_of) == "out_of_window"


def test_no_function_in_counts_accepts_a_count() -> None:
    for name, fn in inspect.getmembers(counts_module, inspect.isfunction):
        if fn.__module__ != counts_module.__name__:
            continue
        for param, value in inspect.signature(fn).parameters.items():
            assert value.annotation not in ("int", int), (name, param)


def test_no_url_is_constructed_from_a_domain() -> None:
    """J13: posting and careers URLs are only ever as returned."""
    for path in (ROOT / "src/jobs").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                literal = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
                assert not re.search(r"https?://", literal), path.name


def test_no_vendor_name_in_the_job_package_or_its_config() -> None:
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    names = [n.lower() for n in vendor_names(load_providers_config()).values() if n != "Parallel"]
    files = [*(ROOT / "src/jobs").glob("*.py")]
    files += [
        ROOT / f"config/{n}" for n in ("job_lanes.yaml", "posting_rubric.yaml", "jobs_v1.yaml")
    ]
    for path in files:
        text = path.read_text().lower()
        assert not [n for n in names if n in text], path.name


def test_every_config_entry_carries_a_source_or_status() -> None:
    import yaml

    lanes = yaml.safe_load((ROOT / "config/job_lanes.yaml").read_text())
    for lane in lanes["lanes"]:
        assert "keyword_source" in lane and ("platforms_source" in lane or lane["platforms"])
        for p in lane["platforms"]:
            assert "source" in p
    rubric = yaml.safe_load((ROOT / "config/posting_rubric.yaml").read_text())
    assert all("status" in r and "source" in r for r in rubric["rules"])
    jobs = yaml.safe_load((ROOT / "config/jobs_v1.yaml").read_text())
    assert all("status" in c and "source" in c for c in jobs["caps"].values())
    assert load_jobs_config().full_text_required_classes == {"build", "admin_owner"}


@settings(max_examples=30)
@given(st.lists(st.sampled_from(["ok", "fail", "unclear", "build"]), max_size=30))
def test_extractor_loop_terminates_within_its_cap(outcomes: list[str]) -> None:
    import asyncio

    from jobs.extractor import ExtractorBudget, classify_with_extractor

    feed = iter(outcomes)
    budget = ExtractorBudget(5)

    async def call(pid: str, text: str, canary: str) -> object:
        kind = next(feed, "fail")
        label = {"ok": "user", "unclear": "unclear", "build": "build"}.get(kind)
        return {"posting_id": pid, "label": label, "evidence_sentence": text} if label else {}

    async def drive() -> int:
        for _ in range(50):
            await classify_with_extractor("p", "Some text.", call, call, "C", budget)
        return budget.remaining

    assert asyncio.run(drive()) == 0  # never more than 5 first passes, whatever the answers


@pytest.mark.parametrize("cls", ["build", "admin_owner"])
def test_full_text_scope_is_one_config_line(cls: str) -> None:
    assert cls in load_jobs_config().full_text_required_classes


def _posting(key: str, **kw: object) -> Posting:
    base: dict[str, object] = dict(
        posting_key=key,
        title="t",
        url="https://e.org/x",
        urls=("https://e.org/x",),
        provider_code="p",
        raw_ref="r",
        retrieved_at="2026-01-01T00:00:00Z",
        window_bucket="in_window",
    )
    base.update(kw)
    return Posting(**base)  # type: ignore[arg-type]


def test_reachable_invariant_violations_raise() -> None:
    from jobs.errors import InvariantViolation

    named_without_sentence = _posting("a", platforms=("SAP",), read=True)
    with pytest.raises(InvariantViolation, match="sentence"):
        compute_counts(_raw(1), [named_without_sentence], ["SAP"], {})
    with pytest.raises(InvariantViolation, match="deduplicated"):
        compute_counts(_raw(2), [_posting("a"), _posting("a")], [], {})
    with pytest.raises(InvariantViolation, match="chain"):
        compute_counts(_raw(0), [_posting("a")], [], {})  # more postings than pulled


def test_claims_only_for_read_classified_named_postings_without_pii() -> None:
    from jobs.claims import claims_for

    text = "Call +1 555 010 9999 about SAP. The SAP developer ships code."
    sentences = (PlatformSentence(platform="SAP", text="x", start=0, end=31, sha256="h"),)
    klass = Classification(label="build", classified_by="rule", reason="r")
    base = dict(platforms=("SAP",), read=True, classification=klass, sentences=sentences)
    assert claims_for(_posting("a", **base), text) == ([], 1)  # PII in the sentence: skipped
    assert claims_for(_posting("b", **{**base, "sentences": ()}), text) == ([], 0)
    assert claims_for(_posting("c", **{**base, "read": False}), text) == ([], 0)
    unclassified = Classification(label="unclassified", classified_by="none", reason="x")
    assert claims_for(_posting("d", **{**base, "classification": unclassified}), text) == ([], 0)
    good = (PlatformSentence(platform="SAP", text="x", start=32, end=61, sha256="h"),)
    made, skipped = claims_for(_posting("e", **{**base, "sentences": good}), text)
    assert skipped == 0 and made[0].evidence[0].supporting_sentence == text[32:61]
    assert made[0].attribute_key == "jobs.sap.build"


def test_config_refuses_unsourced_or_unknown_entries(tmp_path: Path) -> None:
    import shutil

    from jobs.errors import ConfigError

    for name in ("job_lanes.yaml", "posting_rubric.yaml", "jobs_v1.yaml"):
        shutil.copy(ROOT / "config" / name, tmp_path / name)
    load_jobs_config.cache_clear()
    rubric = tmp_path / "posting_rubric.yaml"
    original = rubric.read_text()
    for broken in (
        original.replace("    label: build\n", "    label: wizard\n", 1),
        original.replace(
            '    negative: "Sales representatives use Salesforce every day."\n', "", 1
        ),
        original.replace("  value: [build, admin_owner]", "  value: [build, sorcerer]"),
        original.replace("    status: proposal\n    source: \"class name 'Build role'\"\n", "", 1),
    ):
        rubric.write_text(broken)
        load_jobs_config.cache_clear()
        with pytest.raises(ConfigError):
            load_jobs_config(tmp_path)
    rubric.write_text(original)
    lanes = tmp_path / "job_lanes.yaml"
    lanes.write_text(lanes.read_text().replace("value: platform_names", "value: guess"))
    load_jobs_config.cache_clear()
    with pytest.raises(ConfigError):
        load_jobs_config(tmp_path)
    (tmp_path / "jobs_v1.yaml").write_text("- not a mapping\n")
    load_jobs_config.cache_clear()
    with pytest.raises(ConfigError):
        load_jobs_config(tmp_path)
    (tmp_path / "jobs_v1.yaml").write_text("caps: {}\n")
    load_jobs_config.cache_clear()
    with pytest.raises(ConfigError):
        load_jobs_config(tmp_path)
    load_jobs_config.cache_clear()


def test_jobs_docs_are_generated_and_current() -> None:
    import subprocess
    import sys

    check = subprocess.run([sys.executable, str(ROOT / "scripts/gen_jobs_docs.py"), "--check"])
    assert check.returncode == 0, "docs/jobs is stale"
