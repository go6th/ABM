"""X2 connector contracts (CX1 to CX3, roadmap 11.2; D-88). Lying scenarios: a renamed field, a
type change, a removed field, a new enum value, a nested shape change, a 200 with an error body,
a breaking drift that fails to disable the operation, and a re-enable without a new fixture."""

from __future__ import annotations

from uuid import uuid4

import pytest

from contracts import drift, fingerprint
from contracts import monitor as mon
from account_intelligence.db.session import SessionLocal

P = "p_search"
OP = "web_search"


def _body(status: str = "ok", hits: int = 1) -> dict[str, object]:
    return {
        "status": status,
        "results": [{"title": f"t{hits}", "url": "https://x.example", "published": "2026-01-01"}],
    }


# --- CX1: fingerprints --------------------------------------------------------------------------


def test_the_fingerprint_pins_the_shape_never_the_values() -> None:
    a = fingerprint.fingerprint(_body(hits=1))
    b = fingerprint.fingerprint(_body(hits=2))  # different values, same shape
    assert a == b
    c = fingerprint.fingerprint(_body(status="bad"))
    assert c == a  # enum *values* live in the enum set, not the shape
    d = fingerprint.fingerprint({**_body(), "extra": 1})
    assert d != a  # a new field is a different shape
    with pytest.raises(TypeError):
        fingerprint.shape_of(object())


def test_enum_values_walk_arrays_and_nested_paths() -> None:
    got = fingerprint.enum_values(_body(), "results.status")
    assert got == frozenset()
    got = fingerprint.enum_values({"items": [{"kind": "a"}, {"kind": "b"}]}, "items.kind")
    assert got == frozenset({"a", "b"})


# --- CX2: classification (pure) -----------------------------------------------------------------


def test_additive_changes_are_classified_additive() -> None:
    old = fingerprint.shape_of(_body())
    new = fingerprint.shape_of({**_body(), "region": "us"})
    v = drift.classify(old, new)
    assert v.severity == "additive"
    assert v.changes[0].kind == "field_added"


def test_a_renamed_field_is_a_removal_plus_an_addition_and_breaking() -> None:
    body = _body()
    old = fingerprint.shape_of(body)
    new = fingerprint.shape_of({"status": "ok", "hits": [{"t": "x"}]})
    v = drift.classify(old, new)
    assert v.severity == "breaking"
    kinds = {c.kind for c in v.changes}
    assert "field_removed" in kinds and "field_added" in kinds


def test_a_type_change_a_removal_and_a_nested_change_are_breaking() -> None:
    old = fingerprint.shape_of(_body())
    typed = fingerprint.shape_of({"status": 1, "results": []})
    removed = fingerprint.shape_of({"status": "ok"})
    nested = fingerprint.shape_of({"status": "ok", "results": [{"title": 1}]})
    for new in (typed, removed, nested):
        assert drift.classify(old, new).severity == "breaking"
    # a new enum value is additive; a lost one is breaking
    v = drift.classify(
        old,
        old,
        old_enums={"status": frozenset({"ok"})},
        new_enums={"status": frozenset({"ok", "degraded"})},
    )
    assert v.severity == "additive"
    v = drift.classify(
        old, old, old_enums={"status": frozenset({"ok", "degraded"})}, new_enums={"status": frozenset({"ok"})}
    )
    assert v.severity == "breaking"
    assert v.changes[0].kind == "enum_removed"


def test_a_200_with_an_error_body_is_breaking() -> None:
    assert mon.error_body({"error": "boom"})
    assert mon.error_body({"errors": ["x"]})
    assert not mon.error_body({"error": None})
    assert not mon.error_body({"status": "ok", "results": []})


# --- CX2/CX3: the monitor against the database ---------------------------------------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_breaking_drift_disables_the_operation_fail_closed() -> None:
    async with SessionLocal() as s, s.begin():
        op = f"{OP}_{uuid4().hex[:8]}"  # an isolated operation row
        assert not await mon.is_disabled(s, provider=P, operation=op)
        # the recorded fixture is the contract
        await mon.record_fixture(s, provider=P, operation=op, payload=_body())
        # same shape: quiet
        r = await mon.check(s, provider=P, operation=op, payload=_body())
        assert r.severity == "same" and not r.alert
        # additive: logged, still enabled
        r = await mon.check(s, provider=P, operation=op, payload={**_body(), "region": "us"})
        assert r.severity == "additive" and not r.alert
        assert not await mon.is_disabled(s, provider=P, operation=op)
        # breaking: the results field changed type; disabled, alert, payload kept
        breaking = {"status": "ok", "results": "gone"}
        r = await mon.check(s, provider=P, operation=op, payload=breaking)
        assert r.severity == "breaking" and r.alert
        assert await mon.is_disabled(s, provider=P, operation=op)
        # fail closed: further checks refuse, a caller must mark rows Blocked
        with pytest.raises(mon.OperationDisabled):
            await mon.check(s, provider=P, operation=op, payload=_body())
        # the operator records a new fixture, then re-enables: working again
        await mon.record_fixture(s, provider=P, operation=op, payload={"status": "ok", "results": "gone"}, source="operator")
        await mon.enable(s, provider=P, operation=op, enable_it=True)
        r = await mon.check(s, provider=P, operation=op, payload={"status": "ok", "results": "gone"})
        assert r.severity == "same"
        # the drift trail: one additive, one breaking event with the changes
        rows = (
            (
                await s.execute(
                    __import__("sqlalchemy").text(
                        "SELECT severity, changes FROM contract_drift_events"
                        " WHERE provider_code = :p AND operation = :o ORDER BY id"
                    ),
                    {"p": P, "o": op},
                )
            )
            .all()
        )
        assert [r.severity for r in rows] == ["additive", "breaking"]
        kinds = {c["kind"] for c in rows[1].changes}
        assert "type_changed" in kinds
        payload_row = (
            await s.execute(
                __import__("sqlalchemy").text(
                    "SELECT payload FROM contract_drift_events WHERE severity = 'breaking'"
                    " AND operation = :o"
                ),
                {"o": op},
            )
        ).scalar_one()
        assert payload_row == {"status": "ok", "results": "gone"}
