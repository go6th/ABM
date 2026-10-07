"""Edge cases of the modules held to full coverage (Phase 3 harness: roles, query validation,
parity, limits, scopes, the Drive ancestry check, the audit chain, safe rendering). SYNTHETIC data.

Each case is a lying input the module must refuse or a boundary it must report, not a call made
only to touch a line.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from openpyxl import Workbook

from views import parity, query
from views.render_safe import safe_link
from views.tables import TableSpec, ViewColumn

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SPEC = TableSpec(
    "tech",
    "tech",
    "Tech",
    "Tech table",
    (
        ViewColumn("ref", "Ref", "ref"),
        ViewColumn("name", "Name", "text_short"),
        ViewColumn("seen", "Seen", "date"),
        ViewColumn("hits", "Hits", "count"),
        ViewColumn("tags", "Tags", "label"),
    ),
)


# --- query validation and compilation -------------------------------------------------------


@pytest.mark.parametrize(
    ("flt", "reason"),
    [
        ({"column": "tags", "op": "in", "value": "solo"}, "bad_in_values"),
        ({"column": "tags", "op": "in", "value": []}, "bad_in_values"),
        ({"column": "tags", "op": "in", "value": ["x" * 5000]}, "bad_in_values"),
        ({"column": "hits", "op": "gte", "value": "3"}, "count_needs_integer"),
        ({"column": "hits", "op": "eq", "value": True}, "malformed_query"),  # never the count 1
        ({"column": "name", "op": "eq", "value": ""}, "bad_value"),
        ({"column": "seen", "op": "gte", "value": "March 2026"}, "date_needs_iso"),
    ],
)
def test_query_refuses_malformed_values(flt: dict[str, object], reason: str) -> None:
    with pytest.raises(query.QueryError) as e:
        query.validate(query.parse({"filters": [flt]}), SPEC)
    assert str(e.value) == reason


def test_a_boolean_built_in_code_is_still_not_a_count() -> None:
    lying = query.QuerySpec.model_construct(
        filters=(query.Filter.model_construct(column="hits", op="eq", value=True),),
        sort=(),
        search=None,
        page_size=None,
    )
    with pytest.raises(query.QueryError, match="count_needs_integer"):
        query.validate(lying, SPEC)


def test_query_compiles_every_operator_to_bound_parameters() -> None:
    spec = query.parse(
        {
            "filters": [
                {"column": "name", "op": "ne", "value": "x"},
                {"column": "name", "op": "starts_with", "value": "50%_off\\"},
                {"column": "seen", "op": "gte", "value": "2026-01-01"},
                {"column": "seen", "op": "lte", "value": "2026-12-31"},
                {"column": "hits", "op": "gte", "value": 2},
                {"column": "name", "op": "is_empty"},
                {"column": "seen", "op": "not_empty"},
            ]
        }
    )
    sql, params = query.compile_query(query.validate(spec, SPEC), run_id="r")
    for fragment in ("<> :", "ILIKE :", ">= :", "<= :", "= ''", "<> ''", "::numeric"):
        assert fragment in sql, fragment
    assert "50\\%\\_off\\\\" in params.values()  # LIKE metacharacters escaped, never live
    assert "x" not in sql and "2026" not in sql  # values only as parameters
    with pytest.raises(query.QueryError):
        query._expr("cells'); DROP TABLE view_rows; --", "text_short")
    count_only = TableSpec("tech", "tech", "T", None, (ViewColumn("hits", "Hits", "count"),))
    for op in ("eq", "gte"):
        sql, _ = query.compile_query(
            query.validate(
                query.parse({"filters": [{"column": "hits", "op": op, "value": 1}]}), count_only
            ),
            run_id="r",
        )
        assert "::numeric" in sql
    assert query._json_value(None) == ""
    assert query._json_value(Decimal("2")) == 2 and query._json_value(Decimal("2.5")) == 2.5


# --- parity readers ---------------------------------------------------------------------------


def test_parity_normalises_each_documented_type() -> None:
    assert parity.normalise(None) == ""
    assert (parity.normalise(True), parity.normalise(False)) == ("Yes", "No")
    assert parity.normalise(datetime(2026, 3, 2, 9, tzinfo=UTC)) == "2026-03-02"
    assert parity.normalise(date(2026, 3, 2)) == "2026-03-02"
    assert parity.normalise(3.0) == "3" and parity.normalise(3.5) == "3.5"
    assert parity.normalise("  two\n words ") == "two words"


def _xlsx(rows: list[list[object]], sheet: str = "Tech") -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _docx(tables: list[list[list[str]]]) -> bytes:
    def tbl(rows: list[list[str]]) -> str:
        trs = "".join(
            "<w:tr>"
            + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r)
            + "</w:tr>"
            for r in rows
        )
        return f"<w:tbl>{trs}</w:tbl>"

    body = "".join(tbl(t) for t in tables)
    xml = f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_parity_reports_every_unreadable_or_mismatched_file() -> None:
    api = [{"ref": "T-0001", "name": "a", "seen": "2026-01-01", "hits": "1", "tags": "x"}]
    head = ["Ref", "Name", "Seen", "Hits", "Tags"]
    good = ["T-0001", "a", "2026-01-01", 1, "x"]
    assert parity.check(SPEC, api, "xlsx", _xlsx([head, good])).ok
    missing_sheet = parity.check(SPEC, api, "xlsx", _xlsx([head, good], sheet="Other"))
    assert not missing_sheet.ok and "unreadable" in missing_sheet.problems[0]
    assert not parity.check(SPEC, api, "xlsx", _xlsx([])).ok  # an empty sheet holds no rows
    # a row without a reference (a note, a total) is not a data row
    assert parity.check(SPEC, api, "xlsx", _xlsx([head, good, ["Total", None, None, 1, None]])).ok
    docx = _docx(
        [[], [["Other", "Header"]], [head, [str(v) for v in good], ["no ref", "", "", "", ""]]]
    )
    assert parity.check(SPEC, api, "docx", docx).ok
    assert not parity.check(SPEC, api, "csv", b"").ok
    # a column the table does not define (a reviewer's note) is ignored, never compared
    extra = _xlsx([[*head, "Reviewer note"], [*good, "anything"]])
    assert parity.check(SPEC, api, "xlsx", extra).ok
    csv_extra = "Ref,Name,Seen,Hits,Tags,Reviewer note\nT-0001,a,2026-01-01,1,x,anything\n"
    assert parity.check(SPEC, api, "csv", csv_extra.encode()).ok
    # ...but a changed value in a defined column is caught
    changed = "Ref,Name,Seen,Hits,Tags\nT-0001,b,2026-01-01,1,x\n"
    assert not parity.check(SPEC, api, "csv", changed.encode()).ok
    unknown = parity.check(SPEC, api, "pptx", b"")
    assert not unknown.ok and "unknown file kind" in unknown.problems[0]
    out = parity.check_package({"tech": SPEC}, {"tech": api}, {})
    assert not out["tech"].ok and "missing" in out["tech"].problems[0]


# --- safe links -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://[::1", "https://example.com/a\\b", "javascript:alert(1)", 42, " "]
)
def test_safe_link_refuses_malformed_and_ambiguous_urls(url: object) -> None:
    assert safe_link(url) is None


# --- configuration loaders refuse a lying file ------------------------------------------------


def test_the_role_table_refuses_a_bad_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from identity import roles

    cases = [
        "roles: [admin, seller]\nactions: {}\n",
        "roles: [admin, analyst, seller, viewer]\nactions: {x: [admin, root]}\n",
        "roles: [admin, analyst, seller, viewer]\nactions: {x: []}\n",
        "roles: [admin, analyst, seller, viewer]\nactions: {x: [admin]}\nstep_up: [y]\n",
    ]
    monkeypatch.setattr(roles, "CONFIG_DIR", tmp_path)
    for text in cases:
        (tmp_path / "roles.yaml").write_text(text)
        with pytest.raises(ValueError):
            roles.load_table.__wrapped__()


def test_the_connector_table_refuses_an_unknown_or_missing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    from account_intelligence.config import CONFIG_DIR
    from connectors_client import scopes

    raw = yaml.safe_load((CONFIG_DIR / "connectors.yaml").read_text())
    raw["connectors"].pop(next(iter(raw["connectors"])))
    (tmp_path / "connectors.yaml").write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(scopes, "CONFIG_DIR", tmp_path)
    with pytest.raises(ValueError):
        scopes.load.__wrapped__()


# --- Drive ancestry ---------------------------------------------------------------------------


def test_a_shared_drive_root_is_part_of_the_ancestry_to_resolve() -> None:
    from connectors_client.drive import ancestors_to_resolve
    from connectors_client.ports import DriveMeta

    meta = DriveMeta("f1", "doc", "text/plain", parents=("p1",), drive_id="d1")
    assert ancestors_to_resolve(meta) == ("p1", "d1")
    root = DriveMeta("d1", "drive", "application/vnd.google-apps.folder", drive_id="d1")
    assert ancestors_to_resolve(root) == ()


# --- database: limits and the chain -----------------------------------------------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_plan_without_a_dimension_refuses_and_counters_report_zero() -> None:
    from account_intelligence.db.session import tenant_txn
    from metering import limits
    from tests.conftest import ADMIN_DSN
    from tests.phase3.conftest import new_tenant

    t = new_tenant("edge-plan")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:  # a plan code with no limit rows
        c.execute(
            "INSERT INTO plan_assignments (tenant_id, version, plan_code, assigned_by,"
            " reason_code, created_at) VALUES (%s, 1, 'ghost', 'op', 'synthetic', now())",
            (t,),
        )
    async with tenant_txn(t) as s:
        with pytest.raises(limits.LimitReached):  # closed: no limit means no use
            await limits.consume(s, "accounts", NOW)
        assert set((await limits.counters(s, NOW)).values()) == {0}


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_chain_verification_stops_at_its_batch_cap() -> None:
    from account_intelligence.db.session import tenant_txn
    from audit import chain
    from audit.events import AuditEvent, record
    from tests.phase3.conftest import new_tenant

    t = new_tenant("edge-chain")
    async with tenant_txn(t) as s:
        for i in range(3):
            await record(
                s,
                AuditEvent(
                    at=NOW,
                    actor="u",
                    actor_kind="user",
                    audit_class="admin",
                    action="test.edge",
                    resource_type="thing",
                    resource_id=f"r{i}",
                    route=None,
                    request_id=None,
                ),
            )
    async with tenant_txn(t) as s:
        capped = await chain.verify(s, NOW, batch=1, max_batches=2)
        whole = await chain.verify(s, NOW, batch=10, max_batches=5)
    assert capped.exit_reason == "batch_cap" and capped.ok
    assert whole.exit_reason == "complete" and whole.ok


def test_the_crm_scope_helpers_list_only_grantable_objects() -> None:
    from connectors_client import crm_hubspot, crm_salesforce
    from connectors_client.scopes import spec

    for mod in (crm_salesforce, crm_hubspot):
        assert mod.readable_objects() == tuple(sorted(spec(mod.KIND).objects))
        assert mod.readable_objects()  # each CRM names at least one readable object


@pytest.mark.parametrize(
    ("target_role", "new_role", "new_status", "admins", "expected"),
    [
        ("admin", "seller", "active", 1, "last_admin"),  # demoting the last admin
        ("admin", None, "suspended", 1, "last_admin"),  # suspending the last admin
        ("admin", None, "removed", 1, "last_admin"),
        ("admin", "admin", "active", 1, None),  # no change of role or status
        ("admin", None, "active", 1, None),
        ("admin", "seller", "active", 2, None),  # another admin remains
        ("seller", "viewer", "removed", 1, None),  # not an admin
        ("seller", "admin", "active", 0, None),  # promotion is never a loss
    ],
)
def test_the_last_active_admin_can_never_be_lost(
    target_role: str, new_role: str | None, new_status: str, admins: int, expected: str | None
) -> None:
    from identity.roles import role_change_refusal

    got = role_change_refusal(
        actor="a",
        target="b",
        target_role=target_role,
        new_role=new_role,
        new_status=new_status,
        active_admins=admins,
    )
    assert got == expected


def test_an_unknown_action_names_itself() -> None:
    from identity.roles import UnknownAction, allowed, needs_step_up, roles_for

    for call in (
        lambda: roles_for("drop_tables"),
        lambda: allowed("admin", "drop_tables"),
        lambda: needs_step_up("drop_tables"),
    ):
        with pytest.raises(UnknownAction) as e:
            call()
        assert e.value.args == ("drop_tables",)
