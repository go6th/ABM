"""The verification stage (D-49), run inside ``distill_grade`` right after the support judge and the
facts are recorded (it extends the judge pass: the cheap support judge stays, this adds the
source-level re-read of every Confirmed claim).

1. Each Confirmed fact's stored source text is the run's own stored record whose ``source_ref``
   the evidence cites (the grading engine's ``RunSource``; never refetched).
2. ``verify.claims.verify`` re-reads it blind (router task kind ``source_reread``, judge tier,
   with the generator's family excluded; without a router the legacy port under the judge tier,
   whose independence is the grading engine's own ``judge_profile_ok`` check).
3. Every finding is an insert-only ``verifier_findings`` row (V10); a non-confirmed outcome
   supersedes the fact with the G12-capped claim through ``grading.repo.supersede`` (a new row
   and a grade event, actor ``verifier``, V3). Nothing here edits a value or upgrades a grade.

GUARANTEE(test_fabricated_confirmed_claims_are_downgraded_in_a_whole_run): system test.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.grading import repo
from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.engine import normaliser_for
from account_intelligence.grading.ports import RunInfo, RunSource
from account_intelligence.grading.schema import GradedClaim
from llm.prompts import load
from verify import claims as claims_mod
from verify.config import VerifyConfig
from verify.policy import action_for, cap_claim

PROMPTS = __import__("pathlib").Path(__file__).resolve().parent / "prompts"
# v2 (token review item 10, 2026-10-20): the source opens the user turn as its own cached block
# and the question follows, so the claims read from one source pay its full price once per
# cache window instead of once per claim
PROMPT = "source_reread.v2"


async def generator_family(deps: Any) -> str | None:
    """The family of the candidate that extracted this run's claims (from the call ledger)."""
    async with deps.txn(deps.tenant_id) as s:
        family: Any = (
            await s.execute(
                text(
                    "SELECT family FROM llm_calls WHERE run_id = :r AND task_kind ="
                    " 'extract_facts' ORDER BY id LIMIT 1"
                ),
                {"r": deps.run_id},
            )
        ).scalar_one_or_none()
    return None if family is None else str(family)


def source_block(source_text: str) -> str:
    """The stored source as the cached block that opens a re-read's user turn (identical bytes
    for every claim read from it, so the cache hits)."""
    return f"<source>\n{source_text}\n</source>"


def _ask(deps: Any, family: str | None) -> claims_mod.Ask:
    prompt_text, prompt_hash = load(PROMPT, PROMPTS)

    async def ask(question: str, source_text: str) -> claims_mod.Reread:
        if deps.router is None:
            result = await deps.model(
                "source_reread",
                prompt_text,
                json.dumps({"question": question}),
                schema=claims_mod.Reread.model_json_schema(),
                cached_user=source_block(source_text),
            )
            parsed = claims_mod.Reread.model_validate_json(result.text)
            return parsed
        from llm.models import Scope

        routed = await deps.router(deps).call(
            "source_reread",
            prompt_text,
            prompt_hash,
            {"question": question, "source": source_text},
            claims_mod.Reread,
            Scope(deps.tenant_id, deps.run_id, generator_family=family),
        )
        out: claims_mod.Reread = routed.output
        return out

    return ask


async def verify_confirmed(
    deps: Any,
    run: RunInfo,
    recorded: Sequence[tuple[UUID, GradedClaim]],
    sources: Sequence[RunSource],
    gcfg: GradingConfig,
    vcfg: VerifyConfig,
    legacy_independent: bool,
) -> dict[str, int]:
    """Verify every Confirmed claim; returns counts by outcome (for the run counters)."""
    if not vcfg.enabled:
        return {}
    by_ref = {s.source_ref: s for s in sources}
    todo: list[tuple[UUID, GradedClaim, claims_mod.ClaimToVerify]] = []
    for fact_id, claim in recorded:
        if claim.product_grade != "confirmed":
            continue
        ev = next((e for e in claim.evidence if e.source_ref), None)
        src = by_ref.get(ev.source_ref) if ev and ev.source_ref else None
        todo.append(
            (
                fact_id,
                claim,
                claims_mod.ClaimToVerify(
                    key=str(fact_id),
                    attribute_key=claim.attribute_key,
                    normalised_value=claim.normalised_value,
                    normaliser=normaliser_for(claim.attribute_key, gcfg),
                    source_text=src.text if src else None,
                ),
            )
        )
    if not todo:
        return {}
    family = await generator_family(deps) if deps.router is not None else None
    independent = legacy_independent if deps.router is None else True
    findings = await claims_mod.verify(
        [t[2] for t in todo],
        _ask(deps, family),
        vcfg.max_claims_per_run,
        vcfg.max_source_chars,
        independent,
    )
    by_key = {f.key: f for f in findings}
    counts: dict[str, int] = {}
    _, prompt_hash = load(PROMPT, PROMPTS)
    alias = "router" if deps.router is not None else "legacy_judge"
    for fact_id, claim, _ in todo:
        f = by_key[str(fact_id)]
        counts[f"verify_{f.outcome}"] = counts.get(f"verify_{f.outcome}", 0) + 1
        action = action_for(f.outcome, vcfg.outcome_actions)
        async with deps.txn(deps.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO verifier_findings (run_id, kind, subject_key, fact_id,"
                    " source_text_hash, outcome, action, verifier_alias, prompt_hash,"
                    " span_hashes, at) VALUES (:r, 'claim', :k, :f, :h, :o, :a, :v, :p, :sp,"
                    " :at) ON CONFLICT DO NOTHING"
                ),
                {
                    "r": deps.run_id,
                    "k": claim.claim_hash,
                    "f": fact_id,
                    "h": f.source_text_hash,
                    "o": f.outcome,
                    "a": action,
                    "v": alias,
                    "p": prompt_hash,
                    "sp": [f.quote_hash] if f.quote_hash else [],
                    "at": deps.clock.now(),
                },
            )
        if action == "keep":
            continue
        capped = cap_claim(claim, gcfg.judge.unavailable_cap, f"verifier_{f.outcome}")
        try:
            await repo.supersede(
                deps.txn,
                run,
                fact_id,
                capped,
                reason_code=f"verifier_{f.outcome}",
                actor="verifier",
            )
        except LookupError:
            continue  # already superseded by an earlier pass of this stage (V8)
    return counts
