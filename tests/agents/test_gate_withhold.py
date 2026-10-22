"""A defect in one client file holds back that file, not the whole delivery (2026-10-22).

Until then any failing check ended the run PARTIAL ``gate_failed`` with nothing delivered, and
each fix moved the stop one check later (staging 2026-10-21: banned content, then a leak against
the client's own earlier upload, then a dash in a source title). A run-level failure (a step
without proof, a finding without a source) still holds everything back."""

from __future__ import annotations

import pytest

from agents.graph import FILES_WITHHELD
from tests.agents.conftest import Harness

pytestmark = pytest.mark.asyncio


async def test_a_file_level_defect_withholds_that_file_and_delivers_the_rest(h: Harness) -> None:
    h.gate.failing = {
        "leak_check": ["people:ngram=2,embedding=0,documents=1"],
        "rows_dropped": ["jobs:3", "people:1"],
        "client_dashes": [],
    }
    _, state = await h.run("r")
    assert (state["outcome"], state["error_code"]) == ("partial", FILES_WITHHELD)
    assert state["withheld_files"] == ["jobs", "people"]
    released = [fids for _, fids in h.delivery.released]
    assert released and all(f.startswith("client-") for fids in released for f in fids)
    assert [held for _, held in h.delivery.withheld] == [("jobs", "people")]


async def test_a_run_level_defect_still_holds_every_file_back(h: Harness) -> None:
    h.gate.failing = {"tracker_rows": ["podcasts:unset"], "leak_check": ["people:ngram=1"]}
    _, state = await h.run("r")
    assert (state["outcome"], state["error_code"]) == ("partial", "gate_failed")
    assert not h.delivery.released and not h.delivery.withheld


async def test_an_item_naming_no_client_file_is_run_level(h: Harness) -> None:
    h.gate.failing = {"leak_check": ["note:ngram=1"]}
    _, state = await h.run("r")
    assert (state["outcome"], state["error_code"]) == ("partial", "gate_failed")
    assert not h.delivery.released
