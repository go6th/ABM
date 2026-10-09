"""A fake extractor plus grading engine for Phase 4 simulations (SYNTHETIC only).

Grades by the scripted source tier: t1 is read and Confirmed, t2 read and Probable, t3 read but
Hypothesis; an event type ``keyword_match`` is an unread Lead; ``unverifiable`` returns None. The
fact (with its evidence row: no fact without a source) is recorded once per item; a later
observation of the same item returns the existing fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg

from refresh.probe import SIGNAL_OF, Graded
from signals.registry import Observation
from tests.conftest import ADMIN_DSN

GRADE = {"t1": ("confirmed", True), "t2": ("probable", True), "t3": ("hypothesis", True)}


@dataclass
class FakeGrader:
    """The scripted grader."""

    async def grade(self, tenant: UUID, account_id: UUID, obs: Observation) -> Graded | None:
        """Record (or find) the fact for an observation."""
        if obs.event_type == "unverifiable":
            return None
        grade, read = (
            ("lead", False) if obs.event_type == "keyword_match" else GRADE[obs.source_tier]
        )
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c, c.transaction():
            row = c.execute(
                "SELECT id, product_grade FROM facts WHERE tenant_id = %s AND account_id = %s"
                " AND superseded_at IS NULL AND value_json->>'item_key' = %s",
                (tenant, account_id, obs.item_key),
            ).fetchone()
            if row:
                return Graded(row[0], row[1], read, "S-0001")
            fid = c.execute(
                "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json, fingerprint,"
                " confirming_question, product_grade, read_in_primary_this_run,"
                " may_state_to_prospect) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    tenant,
                    account_id,
                    f"signal.{SIGNAL_OF[obs.kind]}",
                    json.dumps({"item_key": obs.item_key, "kind": obs.kind, "title": obs.title}),
                    uuid4().hex,
                    f"What evidence would confirm {obs.title}?" if grade == "hypothesis" else None,
                    grade,
                    grade == "confirmed",
                    grade == "confirmed",
                ),
            ).fetchone()[0]
            c.execute(
                "INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind,"
                " supporting_sentence, call_ref) VALUES (%s, %s, 'search_proof',"
                " 'synthetic evidence', 'fixture-call')",
                (tenant, fid),
            )
        return Graded(fid, grade, read, "S-0001")
