"""Kill tests for the mutation run of the parity release's pure job modules (2026-10-30):
``jobs.heuristic``, ``jobs.normalise``, ``jobs.snapshot`` and ``jobs.incremental``.

Each test names the behaviour a surviving mutant had left unchecked. Hermetic: no database, no
network, synthetic text only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from jobs import incremental as INC
from jobs import snapshot as SN
from jobs.heuristic import HeuristicRules, classify_heuristic, collapse, platform_windows
from jobs.incremental import (
    OVERLAP_DAYS,
    incremental_input,
    incremental_since,
    postings_since,
)
from jobs.models import Classification, PlatformSentence, Posting, RawPosting
from jobs.normalise import (
    canonical_url,
    clean_text,
    normalise_status,
    posting_key,
    stored_text,
    strip_pii,
)
from jobs.window import window_start

RULES = HeuristicRules(working_tool_cues=("build", "tool"))


# ---- jobs.heuristic ---------------------------------------------------------------------------


def test_the_window_search_ignores_case_unless_told_otherwise() -> None:
    text = "We use sap daily."
    assert platform_windows(text, "SAP", (), RULES) == ("We use sap daily.",)
    assert platform_windows(text, "SAP", (), RULES, case_sensitive=False) == ("We use sap daily.",)
    assert platform_windows(text, "SAP", (), RULES, case_sensitive=True) == ()
    assert platform_windows("We use SAP daily.", "SAP", (), RULES, case_sensitive=True) == (
        "We use SAP daily.",
    )


def test_a_window_starts_after_the_nearest_start_mark_before_the_mention() -> None:
    text = "First. Second * Third. We run Salesforce today. After."
    assert platform_windows(text, "Salesforce", (), RULES) == ("We run Salesforce today.",)
    # the nearest of several marks wins (the last one before the mention, not the first)
    assert platform_windows("A. B. C. Use Salesforce now.", "Salesforce", (), RULES) == (
        "Use Salesforce now.",
    )


def test_a_start_mark_at_the_very_first_character_is_a_mark() -> None:
    assert platform_windows("*Salesforce is our tool.", "Salesforce", (), RULES) == (
        "Salesforce is our tool.",
    )
    assert platform_windows(".Salesforce is our tool.", "Salesforce", (), RULES) == (
        "Salesforce is our tool.",
    )


def test_a_window_without_a_closing_dot_runs_to_the_end_of_the_text() -> None:
    assert platform_windows("We run Salesforce", "Salesforce", (), RULES) == ("We run Salesforce",)
    assert platform_windows("Intro. We run Salesforce", "Salesforce", (), RULES) == (
        "We run Salesforce",
    )


def test_a_closing_dot_at_the_first_character_closes_the_window_there() -> None:
    # a term that matches the empty string at a dot: the window is the dot itself, not the text
    assert platform_windows(".abc", "", (), RULES) == (".",)


def test_the_case_sensitive_set_decides_per_platform() -> None:
    lexicon = {"SAP": ("sap s4",), "Workday": ()}
    only_sap = frozenset({"SAP"})
    # SAP is matched in its own case only; a lower case "sap" names no platform
    assert classify_heuristic("We build sap here.", ("SAP",), lexicon, RULES, only_sap).label == (
        "user"
    )
    # Workday is not in the set: any case names it
    assert (
        classify_heuristic("We build workday here.", ("Workday",), lexicon, RULES, only_sap).label
        == "working_tool"
    )
    # with no set at all both are case insensitive
    assert classify_heuristic("We build sap here.", ("SAP",), lexicon, RULES).label == (
        "working_tool"
    )
    # the platform in the set still matches its own spelling
    assert (
        classify_heuristic("We build SAP here.", ("SAP",), lexicon, RULES, only_sap).label
        == "working_tool"
    )


def test_aliases_of_the_lexicon_open_a_window_too() -> None:
    lexicon = {"ServiceNow": ("snow",)}
    verdict = classify_heuristic("Our snow team builds flows.", ("ServiceNow",), lexicon, RULES)
    assert verdict.label == "working_tool"
    assert verdict.windows == ("Our snow team builds flows.",)
    # a platform the lexicon does not list has no alias: only its own name opens a window
    assert (
        classify_heuristic("Our snow team builds flows.", ("ServiceNow",), {}, RULES).windows == ()
    )


def test_the_verdict_names_the_deciding_window_and_every_window() -> None:
    rules = HeuristicRules(working_tool_cues=("build", "tool"))
    text = "Team lead for Salesforce. We build on Salesforce daily. Salesforce pays well."
    verdict = classify_heuristic(text, ("Salesforce",), {}, rules)
    assert verdict.label == "working_tool"
    assert verdict.evidence == "We build on Salesforce daily."  # first window holding a cue
    assert verdict.windows == (
        "Team lead for Salesforce.",
        "We build on Salesforce daily.",
        "Salesforce pays well.",
    )
    assert isinstance(verdict.windows, tuple)


def test_a_user_verdict_keeps_its_first_window_as_evidence_and_all_windows() -> None:
    verdict = classify_heuristic(
        "A. Salesforce is great. B Salesforce.", ("Salesforce",), {}, RULES
    )
    assert verdict.label == "user"
    assert verdict.evidence == "Salesforce is great."
    assert verdict.windows == ("Salesforce is great.", "B Salesforce.")
    assert isinstance(verdict.windows, tuple)


def test_no_window_is_a_user_role_with_empty_evidence() -> None:
    verdict = classify_heuristic("Nothing named here.", ("Salesforce",), {}, RULES)
    assert (verdict.label, verdict.evidence, verdict.windows) == ("user", "", ())


def test_the_character_cap_counts_one_space_between_windows() -> None:
    rules = HeuristicRules(working_tool_cues=("tool",), max_chars=0)
    first = "Alpha Salesforce is fine."
    second = "Then we use Salesforce as a tool"
    text = f"{first} {second}"
    joined = f"{first} {second}"
    assert len(joined) == len(first) + 1 + len(second)
    exact = HeuristicRules(working_tool_cues=("tool",), max_chars=len(joined))
    assert classify_heuristic(text, ("Salesforce",), {}, exact).label == "working_tool"
    # one character short: the cue is cut off by the cap and does not count
    short = HeuristicRules(working_tool_cues=("tool",), max_chars=len(joined) - 1)
    assert classify_heuristic(text, ("Salesforce",), {}, short).label == "user"
    assert classify_heuristic(text, ("Salesforce",), {}, rules).label == "user"


def test_the_deciding_sentence_is_the_one_that_holds_a_cue_not_one_that_lacks_another() -> None:
    # two cues: the first window holds neither, the second holds one of them
    text = "Salesforce is nice. Salesforce is our build system."
    verdict = classify_heuristic(text, ("Salesforce",), {}, RULES)
    assert verdict.label == "working_tool"
    assert verdict.evidence == "Salesforce is our build system."


def test_cues_are_compared_in_any_case() -> None:
    rules = HeuristicRules(working_tool_cues=("BUILD",))
    assert classify_heuristic("We Build Salesforce.", ("Salesforce",), {}, rules).label == (
        "working_tool"
    )


def test_collapse_joins_whitespace_runs_and_trims() -> None:
    assert collapse("  a \n\t b   c  ") == "a b c"


# ---- jobs.normalise ---------------------------------------------------------------------------


def test_canonical_url_accepts_http_and_https_only() -> None:
    assert canonical_url("http://Example.com/a") == "http://example.com/a"
    assert canonical_url("HTTPS://Example.com/a") == "https://example.com/a"
    assert canonical_url("ftp://example.com/a") == ""
    assert canonical_url("javascript:alert(1)") == ""
    assert canonical_url("https:///nohost") == ""
    assert canonical_url("/relative") == ""


def test_canonical_url_drops_tracking_keeps_the_rest_of_the_query() -> None:
    url = "https://X.com/a/?b=1&utm_source=z&fbclid=2&GCLID=3&Ref=4&source=5&c=&utm_medium=6#frag"
    assert canonical_url(url) == "https://x.com/a?b=1&c="
    assert canonical_url("https://x.com/a?utm_campaign=1") == "https://x.com/a"
    assert canonical_url("https://x.com/a?q=1&q=2") == "https://x.com/a?q=1&q=2"


def test_canonical_url_strips_trailing_slashes_and_keeps_the_root() -> None:
    assert canonical_url("https://x.com/a/b//") == "https://x.com/a/b"
    assert canonical_url("https://x.com") == "https://x.com/"
    assert canonical_url("https://x.com///") == "https://x.com/"
    assert canonical_url("  https://x.com/a  ") == "https://x.com/a"
    assert canonical_url("https://x.com/abX/") == "https://x.com/abX"  # only slashes are stripped


def test_stored_text_and_clean_text_cap_at_one_hundred_thousand_by_default() -> None:
    long = "a" * 100_001
    assert len(stored_text(long)) == 100_000
    assert len(clean_text(long)) == 100_000
    assert len(stored_text(long, 10)) == 10
    assert len(clean_text(long, 10)) == 10
    assert clean_text("x  y\nz") == "x y z"


def test_normalise_status_maps_every_word() -> None:
    for word in ("open", "active", "published", " OPEN ", "Published"):
        assert normalise_status(word) == "open"
    for word in ("closed", "inactive", "expired", "filled", " Expired ", "FILLED"):
        assert normalise_status(word) == "closed"
    assert normalise_status(None) == "open"
    assert normalise_status("draft") == "unknown"
    assert normalise_status("") == "unknown"


def _raw(**kw: Any) -> RawPosting:
    base: dict[str, Any] = {
        "title": "Platform Engineer",
        "description": "Build flows",
        "url": "https://jobs.example/p/1?utm_source=x",
        "provider_code": "S-0001",
        "raw_ref": "ledger/1",
        "retrieved_at": "2026-10-20T00:00:00+00:00",
    }
    return RawPosting(**{**base, **kw})


def test_posting_key_prefers_the_id_then_the_address_then_a_fingerprint() -> None:
    by_id = _raw(source_id="abc")
    assert posting_key(by_id) == "id:abc"
    assert posting_key(by_id, with_strength=True) == ("id:abc", False)
    by_url = _raw()
    assert posting_key(by_url) == "url:https://jobs.example/p/1"
    assert posting_key(by_url, with_strength=True) == ("url:https://jobs.example/p/1", False)


def test_the_fingerprint_is_the_hash_of_the_clean_title_and_description() -> None:
    weak = _raw(url="not a url", title="Platform  Engineer", description="Build <b>flows</b>")
    expected = hashlib.sha256(b"platform engineer build flows").hexdigest()
    assert posting_key(weak, with_strength=True) == ("weak:" + expected, True)
    assert posting_key(weak) == "weak:" + expected
    # no description: the title alone is hashed (after the one separator)
    bare = _raw(url="", description=None, title="Only Title")
    assert posting_key(bare) == "weak:" + hashlib.sha256(b"only title").hexdigest()


def test_strip_pii_removes_email_and_phone() -> None:
    out = strip_pii("Mail hr@jobs.example or +1 555 010 7788 now")
    assert out == "Mail [redacted] or [redacted] now"


# ---- jobs.snapshot ----------------------------------------------------------------------------

WHEN = datetime(2026, 10, 20, 9, tzinfo=UTC)


def _posting(**kw: Any) -> Posting:
    base: dict[str, Any] = {
        "posting_key": "id:p1",
        "title": "Platform Engineer",
        "location": "Springfield, US",
        "url": "https://jobs.example/p1",
        "urls": ("https://jobs.example/p1",),
        "provider_code": "S-0001",
        "raw_ref": "ledger/1",
        "retrieved_at": WHEN.isoformat(),
        "window_bucket": "in_window",
        "status": "open",
        "platforms": ("ServiceNow",),
        "sentences": (
            PlatformSentence(
                platform="ServiceNow", text="Secret flows here.", start=0, end=5, sha256="a"
            ),
        ),
        "classification": Classification(label="build", classified_by="extractor", reason="r"),
    }
    return Posting(**{**base, **kw})


def test_the_identity_names_the_status_day_under_status_since() -> None:
    record = SN.build_snapshot(_posting(), "Plain.", captured_at=WHEN)
    said = json.loads(SN.identity_bytes(record, date(2026, 12, 1)))
    assert said["status_since"] == "2026-12-01"
    assert "captured_at" not in said and "first_seen" not in said
    assert "status_since" not in json.loads(SN.identity_bytes(record))


def test_a_missing_text_has_an_empty_description_and_requisition() -> None:
    record = SN.build_snapshot(_posting(), None, captured_at=WHEN)  # type: ignore[arg-type]
    assert record["description"] == "" and record["requisition"] == ""
    assert SN.requisition_of(None, r"(X+)") == ""  # type: ignore[arg-type]
    assert SN.requisition_of("see XXX", r"(X+)") == "XXX"


def test_a_posting_without_location_or_seniority_has_empty_fields() -> None:
    record = SN.build_snapshot(_posting(location=None, seniority=None), "Plain.", captured_at=WHEN)
    assert record["location"] == "" and record["seniority"] == ""


def test_class_label_prints_the_configured_label_else_the_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        SN,
        "export_rule_texts",
        lambda: {"pattern": "", "classes": {"build": "Build role", "unclassified": "Unclassified"}},
    )
    assert SN.class_label("build") == "Build role"
    assert SN.class_label(None) == "Unclassified"
    assert SN.class_label("") == "Unclassified"
    assert SN.class_label("surprise") == "surprise"
    monkeypatch.setattr(SN, "export_rule_texts", lambda: {"pattern": "", "classes": {}})
    assert SN.class_label(None) == ""  # no label for the unclassified id: blank, never a marker
    assert SN.class_label("") == ""


def test_a_refused_sentence_alone_is_named_under_withheld() -> None:
    def guard(text: str) -> tuple[str, bool]:
        return text, "Secret" in text

    record = SN.build_snapshot(_posting(), "Plain text.", captured_at=WHEN, guard=guard)
    assert record["withheld"] == ["platform_sentences"]
    assert record["platform_sentences"] == []
    assert record["description"] == "Plain text."


def test_a_refused_sentence_joins_the_fields_already_withheld() -> None:
    def guard(text: str) -> tuple[str, bool]:
        return text, "Secret" in text or "Springfield" in text

    record = SN.build_snapshot(_posting(), "Plain text.", captured_at=WHEN, guard=guard)
    assert record["withheld"] == ["location", "platform_sentences"]


# ---- jobs.incremental -------------------------------------------------------------------------


def test_the_since_bound_reads_the_first_ten_characters_of_an_iso_value() -> None:
    assert postings_since({"postings_since": "2026-10-30"}) == date(2026, 10, 30)
    assert postings_since({"postings_since": "2026-10-30T12:00:00+00:00"}) == date(2026, 10, 30)
    assert postings_since({"postings_since": "2026-10-3x"}) is None
    assert postings_since({"postings_since": ""}) is None
    assert postings_since({}) is None
    assert postings_since(None) is None


def test_a_previous_research_dated_the_window_start_is_a_delta() -> None:
    as_of = date(2026, 10, 30)
    start = window_start(as_of)
    assert incremental_since(start, as_of) == start  # on the boundary: still inside
    assert incremental_since(start - timedelta(days=1), as_of) is None
    assert incremental_since(as_of, as_of) == as_of - timedelta(days=OVERLAP_DAYS)
    assert incremental_since(as_of + timedelta(days=1), as_of) is None
    assert incremental_since(start + timedelta(days=3), as_of) == start  # never before the window


def test_the_run_input_carries_the_previous_run_and_the_bound() -> None:
    from uuid import UUID

    run = UUID("00000000-0000-0000-0000-000000000009")
    as_of = date(2026, 10, 30)
    assert incremental_input(None, as_of) == {}
    assert incremental_input((run, as_of - timedelta(days=400)), as_of) == {}
    out = incremental_input((run, date(2026, 10, 20)), as_of)
    assert out == {
        "postings_previous_run": str(run),
        "postings_since": (date(2026, 10, 20) - timedelta(days=OVERLAP_DAYS)).isoformat(),
    }


# ---- jobs.incremental: which stored research is a safe base (fake session) --------------------


RUN_A = UUID("00000000-0000-0000-0000-0000000000a1")
RUN_B = UUID("00000000-0000-0000-0000-0000000000b2")
ACCOUNT = UUID("00000000-0000-0000-0000-0000000000c3")

SELECT = (
    "SELECT j.run_id, j.as_of, j.config_hash,"
    " j.record_json->'exit_reasons'->>'pull' AS pull,"
    " j.record_json->>'invariant_error' AS invariant_error,"
    " j.record_json->>'pull_complete' AS pull_complete,"
    " j.record_json->>'lexicon_hash' AS lexicon_hash,"
    " j.record_json->'lanes_off' AS lanes_off,"
    " j.record_json->'lanes_not_searched' AS lanes_not_searched"
    " FROM job_pipeline_runs j"
    " JOIN account_runs r ON r.id = j.run_id AND r.tenant_id = j.tenant_id"
    " WHERE j.account_id = :a AND r.account_id = :a AND r.status = ANY(:ok)"
    " AND EXISTS (SELECT 1 FROM deliverables d WHERE d.run_id = j.run_id"
    " AND d.tenant_id = j.tenant_id AND d.account_id = :a)"
    " ORDER BY j.as_of DESC, j.created_at DESC LIMIT :n"
)


def _row(run: UUID = RUN_A, as_of: date = date(2026, 10, 1), **kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "run_id": run,
        "as_of": as_of,
        "config_hash": "cfg",
        "pull": "converged",
        "invariant_error": None,
        "pull_complete": "true",
        "lexicon_hash": "lex",
        "lanes_off": [],
        "lanes_not_searched": None,
    }
    return SimpleNamespace(**{**base, **kw})


class _Result:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self._rows


class _Session:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append((str(statement), params))
        return _Result(self.rows)


def _previous(rows: list[SimpleNamespace], matching: INC.Matching | None = None) -> Any:
    session = _Session(rows)
    found = asyncio.run(INC.previous_delivered_run(session, ACCOUNT, matching))
    return found, session


MATCH = INC.Matching(frozenset(), "lex", "cfg")


def test_the_lookup_asks_for_the_newest_delivered_research_of_the_account() -> None:
    found, session = _previous([_row()])
    assert found == (RUN_A, date(2026, 10, 1))
    ((statement, params),) = session.calls
    assert statement == SELECT
    assert params == {"a": ACCOUNT, "ok": ["COMPLETED", "PARTIAL", "BUDGET_EXCEEDED"], "n": 20}


def test_no_stored_research_is_no_base() -> None:
    assert _previous([])[0] is None


def test_the_first_safe_row_in_the_order_given_is_the_base() -> None:
    rows = [_row(RUN_A, pull="exhausted"), _row(RUN_B, date(2026, 9, 1))]
    assert _previous(rows)[0] == (RUN_B, date(2026, 9, 1))
    assert _previous(list(reversed(rows)))[0] == (RUN_B, date(2026, 9, 1))


@pytest.mark.parametrize(
    "bad",
    [
        {"pull": "capped"},
        {"pull": None},
        {"invariant_error": "boom"},
        {"pull_complete": "false"},
        {"pull_complete": "False"},
    ],
)
def test_a_pull_that_did_not_converge_completely_is_no_base(bad: dict[str, Any]) -> None:
    assert _previous([_row(**bad)])[0] is None
    assert _previous([_row(**bad)], MATCH)[0] is None


@pytest.mark.parametrize("complete", [None, "true", "True", "TRUE"])
def test_a_record_that_predates_the_completeness_flag_or_says_true_is_a_base(complete: Any) -> None:
    assert _previous([_row(pull_complete=complete)], MATCH)[0] == (RUN_A, date(2026, 10, 1))


def test_without_a_matching_the_lanes_and_lexicon_are_not_compared() -> None:
    row = _row(lanes_off=["sap"], lexicon_hash="other")
    assert _previous([row])[0] == (RUN_A, date(2026, 10, 1))


def test_the_lanes_switched_off_must_equal_the_current_ones() -> None:
    assert _previous([_row(lanes_off=["sap"])], MATCH)[0] is None
    sap = INC.Matching(frozenset({"sap"}), "lex", "cfg")
    assert _previous([_row(lanes_off=["sap"])], sap)[0] == (RUN_A, date(2026, 10, 1))
    assert _previous([_row(lanes_off=[])], sap)[0] is None
    # a record that predates ``lanes_off`` names the lanes it did not search for scope
    old = _row(lanes_off=None, lanes_not_searched={"sap": "lane_not_in_scope", "x": "other"})
    assert _previous([old], sap)[0] == (RUN_A, date(2026, 10, 1))
    assert _previous([old], MATCH)[0] is None
    # neither list: nothing was off
    assert _previous([_row(lanes_off=None, lanes_not_searched=None)], MATCH)[0] is not None
    assert _previous([_row(lanes_off=None, lanes_not_searched=None)], sap)[0] is None


def test_the_lexicon_hash_decides_else_the_config_hash() -> None:
    assert _previous([_row(lexicon_hash="stale")], MATCH)[0] is None
    assert _previous([_row(lexicon_hash="lex", config_hash="whatever")], MATCH)[0] is not None
    assert _previous([_row(lexicon_hash=None, config_hash="cfg")], MATCH)[0] is not None
    assert _previous([_row(lexicon_hash="", config_hash="cfg")], MATCH)[0] is not None
    assert _previous([_row(lexicon_hash=None, config_hash="stale")], MATCH)[0] is None


def test_the_found_run_id_is_a_uuid() -> None:
    found, _ = _previous([_row(run="00000000-0000-0000-0000-0000000000a1")])
    assert found is not None and isinstance(found[0], UUID) and found[0] == RUN_A


def test_the_current_matching_is_the_configurations_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    import jobs.config as JC

    monkeypatch.setattr(
        JC, "load_jobs_config", lambda: SimpleNamespace(lexicon_hash="L", config_hash="C")
    )
    assert INC.current_matching(["b", "a"]) == INC.Matching(frozenset({"a", "b"}), "L", "C")
    assert INC.current_matching(None).lanes_off == frozenset()
    assert INC.current_matching([]).lanes_off == frozenset()


def test_previous_run_id_reads_a_uuid_only() -> None:
    assert INC.previous_run_id({"postings_previous_run": str(RUN_A)}) == str(RUN_A)
    assert INC.previous_run_id({"postings_previous_run": "nope"}) is None
    assert INC.previous_run_id({"postings_previous_run": ""}) is None
    assert INC.previous_run_id(None) is None
