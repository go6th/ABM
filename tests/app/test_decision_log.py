"""Decision log: structure ready for sign-off, evidence slots written safely."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.benchmarks.common import Provenance
from app.decision_log import DECISIONS, DEFAULT_LOG, EvidenceRefused, markers, write_evidence

ROLES = ("Tech lead", "Analyst owner", "Product owner")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def log(tmp_path: Path) -> Path:
    dst = tmp_path / "decision_log.md"
    shutil.copy(DEFAULT_LOG, dst)
    return dst


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n## ", start + 1)
    return text[start : nxt if nxt != -1 else None]


@pytest.mark.parametrize(
    ("heading", "key"),
    [
        ("## D1. Workflow engine", "workflow_engine"),
        ("## D2. Model split", "model_split"),
        ("## D3. People source", "people_source"),
    ],
)
def test_each_decision_is_ready_for_sign_off(heading: str, key: str) -> None:
    section = _section(DEFAULT_LOG.read_text(encoding="utf-8"), heading)
    assert "**Status:** Proposed" in section
    for part in ("**Options**", "**Recommendation.**", "**Evidence**", "**Sign-off**"):
        assert part in section
    begin, end = markers(key)
    assert begin in section and end in section
    for role in ROLES:
        assert re.search(rf"^\| {role} \| \| \| \|$", section, re.M), role


def test_workflow_engine_records_the_recommended_default() -> None:
    section = _section(DEFAULT_LOG.read_text(encoding="utf-8"), "## D1. Workflow engine")
    assert "Temporal for orchestration, with agent steps as activities" in section
    assert "LangGraph only inside" in section
    assert "**Comparison criteria**" in section


def test_shipped_log_holds_no_fake_evidence() -> None:
    text = DEFAULT_LOG.read_text(encoding="utf-8")
    assert "NOT EVIDENCE:" not in text
    assert "Generated " not in text


def test_non_evidence_is_refused(log: Path) -> None:
    before = log.read_text()
    with pytest.raises(EvidenceRefused):
        write_evidence(log, "model_split", "x", Provenance("fake", "synthetic"), config_version="v")
    assert log.read_text() == before


def test_allowed_non_evidence_is_stamped_and_only_its_slot_changes(log: Path) -> None:
    before = log.read_text()
    write_evidence(
        log,
        "people_source",
        "| a |\n| --- |\n| 1 |",
        Provenance("recorded", "synthetic"),
        config_version="cfg-1",
        allow_non_evidence=True,
        now=NOW,
    )
    after = log.read_text()
    begin, end = markers("people_source")
    slot = after[after.index(begin) : after.index(end)]
    assert "NOT EVIDENCE" in slot
    assert "2026-10-01 12:00 UTC" in slot
    assert "`cfg-1`" in slot
    # Everything outside the slot is untouched, including all sign-off tables.
    b0, e0 = before.index(begin), before.index(end)
    b1, e1 = after.index(begin), after.index(end)
    assert before[:b0] == after[:b1]
    assert before[e0:] == after[e1:]


def test_real_evidence_has_no_banner_and_rewrites_cleanly(log: Path) -> None:
    for body in ("first", "second"):
        write_evidence(
            log, "model_split", body, Provenance("live", "real"), config_version="v", now=NOW
        )
    text = log.read_text()
    begin, end = markers("model_split")
    slot = text[text.index(begin) : text.index(end)]
    assert "second" in slot and "first" not in slot
    assert "NOT EVIDENCE" not in slot
    assert text.count(begin) == 1


def test_unknown_decision_and_missing_slot(log: Path, tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        write_evidence(log, "pricing", "x", Provenance("live", "real"), config_version="v")
    bare = tmp_path / "bare.md"
    bare.write_text("# nothing here\n")
    with pytest.raises(ValueError, match="no evidence slot"):
        write_evidence(bare, "model_split", "x", Provenance("live", "real"), config_version="v")


def test_decision_keys() -> None:
    assert DECISIONS == ("workflow_engine", "model_split", "people_source")
