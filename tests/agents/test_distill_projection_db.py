"""The client distill output projected to ``report_sections`` (``PgRunRecords.save_distill``,
audit 2026-10-15): a narrative sentence that states a fact a client file may not state is
withheld and counted; when no narrative sentence survives, the first-call questions open the
Summary instead, so a research with only Probable findings still builds its files."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from agents.ports import RunContext
from agents.production.state import PgRunRecords
from agents.schemas import Claim, DistillOutput
from tests.conftest import ADMIN_DSN, _make_account

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: object) -> list:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _source(c: Any, tenant: UUID, account: UUID, run: UUID, code: str, url: str) -> UUID:
    row = c.execute(
        "INSERT INTO sources (tenant_id, account_id, source_code, url, url_normalized,"
        " url_hash, kind, first_seen_run_id, last_seen_run_id) VALUES (%s, %s, %s, %s, %s,"
        " %s, 'primary', %s, %s) RETURNING id",
        (tenant, account, code, url, url, hashlib.sha256(url.encode()).hexdigest(), run, run),
    ).fetchone()
    return UUID(str(row[0]))


def _world(tenant: UUID) -> tuple[UUID, UUID, dict[str, str]]:
    """An account, a run and three public facts in one transaction (a fact needs its evidence
    row at commit): confirmed and probable, both cited from a registered source, and one whose
    evidence names no registered source (no source code to cite)."""
    account = _make_account(tenant, f"distill-{uuid4().hex[:8]}.example")
    facts: dict[str, str] = {}
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
            " reserved_credits, idempotency_key, status) VALUES (%s, %s, 'test', '{}', 1,"
            " %s, 'DISTILLING') RETURNING id",
            (tenant, account, uuid4().hex),
        ).fetchone()
        run = UUID(str(row[0]))
        c.execute(  # sources point at the tracked run row too
            "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
            " credit_cap, started_at) VALUES (%s, %s, %s, 'full', 'running', 'test', 1, now())",
            (run, tenant, account),
        )
        url = f"https://{uuid4().hex[:6]}.example/about"
        source = _source(c, tenant, account, run, "S-0001", url)
        for key, grade, cited in (
            ("confirmed", "confirmed", True),
            ("probable", "probable", True),
            ("uncited", "probable", False),
        ):
            fid = uuid4()
            c.execute(
                "INSERT INTO facts (id, tenant_id, account_id, attribute_key, value_json,"
                " fingerprint, engine_grade, product_grade, read_in_primary_this_run,"
                " may_state_to_prospect, status, visibility, run_id, claim_kind, value_text)"
                " VALUES (%s, %s, %s, %s, to_jsonb(%s::text), %s, 'A', %s, %s, %s, 'current',"
                " 'public', %s, 'sourced', %s)",
                (
                    fid,
                    tenant,
                    account,
                    f"company.company_news.{key}",
                    key,
                    uuid4().hex,
                    grade,
                    grade == "confirmed",
                    grade == "confirmed",
                    run,
                    f"value {key}",
                ),
            )
            c.execute(
                "INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind, url,"
                " supporting_sentence, span_sha256, source_id, source_tier, origin, provider_code,"
                " evidence_date) VALUES (%s, %s, %s, %s, %s, %s, %s, 'T1', %s, 'p_search', now())",
                (
                    tenant,
                    fid,
                    "source",
                    url,
                    f"A sentence about {key}.",
                    hashlib.sha256(key.encode()).hexdigest(),
                    source if cited else None,  # no registered source: nothing to cite
                    "full_page_read" if cited else "search_snippet",
                ),
            )
            facts[key] = str(fid)
        c.commit()
    return account, run, facts


def _ctx(tenant: UUID, run: UUID) -> RunContext:
    return RunContext(tenant, run, "acme", "Acme", "acme.example", False, "sk-1")


def _sections(run: UUID) -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in _db(
            "SELECT kind, text FROM report_sections WHERE run_id = %s ORDER BY kind, position", run
        )
    ]


async def test_a_statement_about_a_probable_fact_is_withheld_and_a_question_opens_instead(
    tenant_a: UUID,
) -> None:
    _, run, f = _world(tenant_a)
    out = DistillOutput(
        narrative=(
            Claim(text="The account is investing in a platform.", fact_ids=(f["probable"],)),
            Claim(text="A sentence with no source behind it.", fact_ids=(f["uncited"],)),
        ),
        questions=(Claim(text="Is the platform work the priority?", fact_ids=(f["probable"],)),),
        hypotheses=(Claim(text="The account may be consolidating.", fact_ids=(f["probable"],)),),
    )
    await PgRunRecords(tenant_txn).save_distill(_ctx(tenant_a, run), "client", out)
    sections = _sections(run)
    assert ("narrative", "Is the platform work the priority?") in sections
    assert all(k != "narrative" or t.endswith("?") for k, t in sections)  # nothing stated
    assert ("first_call_question", "Is the platform work the priority?") in sections
    assert ("hypothesis", "The account may be consolidating.") in sections
    # replays are idempotent: the same output projects nothing twice
    await PgRunRecords(tenant_txn).save_distill(_ctx(tenant_a, run), "client", out)
    assert _sections(run) == sections


async def test_a_statement_about_a_confirmed_fact_is_kept_as_written(tenant_a: UUID) -> None:
    _, run, f = _world(tenant_a)
    out = DistillOutput(
        narrative=(Claim(text="The account runs its own platform.", fact_ids=(f["confirmed"],)),),
        questions=(Claim(text="Is the platform work the priority?", fact_ids=(f["probable"],)),),
    )
    await PgRunRecords(tenant_txn).save_distill(_ctx(tenant_a, run), "client", out)
    narrative = [t for k, t in _sections(run) if k == "narrative"]
    assert narrative == ["The account runs its own platform."]  # no question fallback needed
