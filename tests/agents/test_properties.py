"""Property-based tests (Hypothesis) of the guard decisions: invariants over generated inputs."""

from __future__ import annotations

import string
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from agents.errors import InvalidMode, NoPulledData
from agents.guards import coverage_verdict, evidence_problem, lead_drop_decision, phrasing_key
from agents.manifest import ManifestRow, PullManifest, QueryRecord, check_build_precondition
from agents.modes import RunMode, resolve_mode
from agents.naming import client_hits, provider_terms
from agents.ports import RowOutcome
from agents.schemas import EvidenceRef, PayloadView

T0 = datetime(2026, 10, 8, tzinfo=UTC)
WORDS = st.text(alphabet=string.ascii_lowercase, min_size=2, max_size=8)
STATUS = st.sampled_from(["done", "partial", "not_done", "blocked", "none", None, "running"])


@given(st.lists(WORDS, min_size=1, max_size=6), st.randoms())
def test_reordering_recasing_or_punctuating_is_not_a_new_phrasing(words, rnd) -> None:  # type: ignore[no-untyped-def]
    shuffled = list(words)
    rnd.shuffle(shuffled)
    variant = ", ".join(w.upper() if rnd.random() < 0.5 else w for w in shuffled) + "!"
    assert phrasing_key(" ".join(words)) == phrasing_key(variant)


@given(
    st.lists(
        st.tuples(
            st.lists(WORDS, min_size=1, max_size=4), st.sampled_from(["web", "news", "jobs"])
        ),
        max_size=8,
    ),
    st.lists(WORDS, min_size=1, max_size=4),
    st.sampled_from(["web", "news", "jobs"]),
)
def test_another_query_never_revokes_an_allowed_drop(attempts, extra, tool) -> None:  # type: ignore[no-untyped-def]
    records = [QueryRecord(text=" ".join(w), tool_category=t, result_count=0) for w, t in attempts]
    before = lead_drop_decision(records, 3, 2)
    after = lead_drop_decision(
        [*records, QueryRecord(text=" ".join(extra), tool_category=tool, result_count=0)], 3, 2
    )
    assert not before.allowed or after.allowed
    assert after.phrasings >= before.phrasings and after.tool_categories >= before.tool_categories


@given(
    st.dictionaries(
        WORDS, st.tuples(STATUS, st.one_of(st.none(), WORDS), st.integers(0, 3)), max_size=12
    ),
    st.lists(WORDS, max_size=12),
)
def test_coverage_is_deliverable_exactly_when_every_row_is_proven(rows, required) -> None:  # type: ignore[no-untyped-def]
    outcomes = {k: RowOutcome(s, r, q) for k, (s, r, q) in rows.items()}

    def proven(key: str) -> bool:
        o = outcomes.get(key)
        if o is None or o.status not in ("done", "partial", "not_done", "blocked", "none"):
            return False
        if o.status == "done" and o.queries <= 0:  # a Partial row proves itself by its reason
            return False
        return not (o.status != "done" and not o.reason)

    verdict = coverage_verdict(required, outcomes)
    assert verdict.deliverable == all(proven(k) for k in required)
    assert set(verdict.missing) | set(verdict.unproven) == {k for k in required if not proven(k)}


@given(
    st.sets(st.sampled_from([f"r{i}" for i in range(10)]), min_size=1),
    st.sets(st.sampled_from([f"r{i}" for i in range(10)])),
)
def test_a_build_never_passes_with_a_missing_row(required, present) -> None:  # type: ignore[no-untyped-def]
    tenant = uuid4()
    manifest = PullManifest(
        tenant_id=tenant,
        run_id=uuid4(),
        account_key="a",
        config_version="v",
        created_at=T0,
        rows=tuple(
            ManifestRow(
                row_key=k, status="stored", payload_refs=("p",), call_refs=("c",), pulled_at=T0
            )
            for k in sorted(present)
        ),
    )
    try:
        check_build_precondition(
            manifest,
            tenant_id=tenant,
            account_key="a",
            required_rows=required,
            shelf_life_days={},
            now=T0,
        )
    except NoPulledData as error:
        assert set(error.missing) == required - present
    else:
        assert required <= present


@given(st.text(max_size=12))
def test_only_the_three_exact_tokens_are_modes(value: str) -> None:
    try:
        mode = resolve_mode(value)
    except InvalidMode:
        assert value not in ("full", "pull", "build")
    else:
        assert mode in RunMode and value == mode.value


@given(
    st.text(alphabet=string.ascii_letters + " .,", min_size=20, max_size=200),
    st.integers(0, 150),
    st.integers(8, 40),
)
def test_a_quote_is_accepted_only_when_it_is_in_the_payload(text, start, length) -> None:  # type: ignore[no-untyped-def]
    payload = PayloadView(
        payload_ref="p", row_key="company_news", url="https://a.example/x", text=text
    )
    quote = text[start : start + length]
    if len(quote.strip()) < 8:
        return
    ok = evidence_problem((EvidenceRef(payload_ref="p", quote=quote),), {"p": payload})
    assert ok is None
    forged = evidence_problem(
        (EvidenceRef(payload_ref="p", quote=quote + " ZZQX fabricated"),), {"p": payload}
    )
    assert forged == "quote_not_in_payload"


@settings(max_examples=50)
@given(st.text(alphabet=string.ascii_letters + " ", max_size=60), st.data())
def test_a_provider_name_anywhere_is_always_caught(prefix: str, data: st.DataObject) -> None:
    term = data.draw(st.sampled_from(provider_terms()))
    assert client_hits(f"{prefix} {term} wrote this")


@given(st.integers(0, 400))
def test_stale_rows_are_exactly_those_past_their_shelf_life(age_days: int) -> None:
    from agents.manifest import stale_rows

    manifest = PullManifest(
        tenant_id=uuid4(),
        run_id=uuid4(),
        account_key="a",
        config_version="v",
        created_at=T0,
        rows=(
            ManifestRow(
                row_key="news",
                status="stored",
                payload_refs=("p",),
                call_refs=("c",),
                pulled_at=T0 - timedelta(days=age_days),
            ),
        ),
    )
    assert stale_rows(manifest, {"news": 7}, T0) == (("news",) if age_days > 7 else ())
