"""Recorded-response broker doubles and the pull-once session (roadmap s3 principle 2, s11.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.benchmarks.people_source import bitscale_request, sumble_request
from app.brokers import (
    BrokerError,
    BrokerRequest,
    BrokerResponse,
    BrokerSession,
    RecordedConnector,
    RecordingNotFound,
    write_recording,
)

DOMAIN = "alpha-synthetic.example"


def test_cache_key_is_stable_and_order_independent() -> None:
    a = BrokerRequest(broker="sumble", operation="x", params={"a": 1, "b": [1, 2]})
    b = BrokerRequest(broker="sumble", operation="x", params={"b": [1, 2], "a": 1})
    c = BrokerRequest(broker="sumble", operation="x", params={"a": 2, "b": [1, 2]})
    assert a.cache_key() == b.cache_key() != c.cache_key()


def test_neutral_name_hides_broker_and_account() -> None:
    req = bitscale_request(DOMAIN)
    name = req.neutral_name()
    assert "bitscale" not in name
    assert "alpha" not in name
    assert name.startswith("raw_") and name.endswith(".json")


def test_recorded_connector_serves_recordings() -> None:
    conn = RecordedConnector("bitscale")
    resp = conn.call(bitscale_request(DOMAIN))
    assert resp.cost_credits == 0
    assert len(resp.payload["people"]) == 7
    assert conn.served == [bitscale_request(DOMAIN).cache_key()]


def test_every_recording_is_labelled_synthetic() -> None:
    root = Path(__file__).resolve().parents[2] / "fixtures" / "recorded"
    files = list(root.rglob("*.json"))
    assert files
    for f in files:
        assert json.loads(f.read_text(encoding="utf-8"))["synthetic"] is True


def test_missing_recording_never_falls_back_to_network() -> None:
    req = BrokerRequest(broker="bitscale", operation="find_people", params={"domain": "x.example"})
    with pytest.raises(RecordingNotFound):
        RecordedConnector("bitscale").call(req)


def test_connector_rejects_other_brokers() -> None:
    with pytest.raises(BrokerError, match="connector got"):
        RecordedConnector("sumble").call(bitscale_request(DOMAIN))


def test_tampered_recording_is_rejected(tmp_path: Path) -> None:
    req = sumble_request(DOMAIN)
    path = write_recording(req, {"people": []}, 1, synthetic=True, root=tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["request"]["params"]["domain"] = "other.example"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BrokerError, match="does not match"):
        RecordedConnector("sumble", root=tmp_path).call(req)


def test_write_recording_round_trip(tmp_path: Path) -> None:
    req = BrokerRequest(broker="firecrawl", operation="scrape", params={"url": "https://a.example"})
    write_recording(req, {"markdown": "hi"}, 1, synthetic=True, root=tmp_path)
    resp = RecordedConnector("firecrawl", root=tmp_path).call(req)
    assert resp.payload == {"markdown": "hi"}
    assert resp.cost_credits == 1


def test_session_pulls_once_and_tracks_spend() -> None:
    session = BrokerSession(
        {"bitscale": RecordedConnector("bitscale"), "sumble": RecordedConnector("sumble")}
    )
    first = session.call(sumble_request(DOMAIN))
    again = session.call(sumble_request(DOMAIN))
    session.call(bitscale_request(DOMAIN))
    assert again == first
    assert session.paid_pulls() == 2
    assert session.duplicate_paid_pulls() == []
    assert session.total_spend() == first.cost_credits
    assert session.spend_by_broker() == {"sumble": first.cost_credits, "bitscale": 0.0}
    assert [c.served_from_cache for c in session.calls] == [False, True, False]
    assert sumble_request(DOMAIN).neutral_name() in session.raw_store


def test_session_without_connector_fails() -> None:
    with pytest.raises(BrokerError, match="no connector"):
        BrokerSession({}).call(bitscale_request(DOMAIN))


class _WrongKeyConnector:
    broker = "bitscale"

    def call(self, request: BrokerRequest) -> BrokerResponse:
        return BrokerResponse(request_key="nope", payload={}, cost_credits=0)


def test_session_rejects_mismatched_response() -> None:
    with pytest.raises(BrokerError, match="different request"):
        BrokerSession({"bitscale": _WrongKeyConnector()}).call(bitscale_request(DOMAIN))


def test_duplicate_paid_pulls_are_detected() -> None:
    session = BrokerSession({})
    from app.brokers.base import CallRecord

    session.calls += [CallRecord("k", "b", "op", 1, False), CallRecord("k", "b", "op", 1, False)]
    assert session.duplicate_paid_pulls() == ["k"]
