"""The cost shown before a file-mode run starts.

* Model: the prose passages, batched as the extractor batches them, priced at the extraction
  route of the active arm in ``config/models.yaml`` (input from the passage text and the
  instructions; output at most the task's token limit per batch, estimated at a quarter of it).
* Embedding: what the configured embedder charges (the local embedder costs nothing).
* Enrichment (opt-in): the public research run spends at most its cap; the exact plan is shown
  at its cap approval, as for any company-mode run.

GUARANTEE(test_estimate_is_shown_before_start)
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR
from documents import extract as ex
from documents.config import load_config


@lru_cache(maxsize=1)
def _extraction_price() -> tuple[str | None, float, float]:
    data = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
    arm = data.get("arms", {}).get(data.get("active_arm") or "automatic", {})
    profile = data.get("profiles", {}).get(arm.get("routes", {}).get("extraction", ""), {})
    return (
        profile.get("model"),
        float(profile.get("input_usd_per_mtok") or 0),
        float(profile.get("output_usd_per_mtok") or 0),
    )


async def estimate(
    s: Any, document_ids: Sequence[UUID], enrich: bool, cap_usd: float
) -> dict[str, Any]:
    """See the module docstring (documents of this tenant only; others are not counted)."""
    from llm.tasks import require

    cfg = load_config()
    rows = (
        await s.execute(
            text(
                "SELECT p.document_id, p.kind, p.content FROM document_passages p"
                " WHERE p.document_id = ANY(:ids) ORDER BY p.document_id, p.seq"
            ),
            {"ids": list(document_ids)},
        )
    ).all()
    docs = (
        await s.execute(
            text("SELECT count(*) FROM documents WHERE id = ANY(:ids)"), {"ids": list(document_ids)}
        )
    ).scalar_one()
    prose = [r for r in rows if r.kind != "crm_row"]
    batches = ex.batches(prose, cfg)
    per_token = cfg.chunking.chars_per_token
    input_tokens = int(
        sum(len(ex.SYSTEM) + sum(len(r.content) + 120 for _, r in b) for b in batches) / per_token
    )
    max_out = require(cfg.extraction.task_kind).max_tokens
    output_tokens = len(batches) * max_out // 4
    model, in_price, out_price = _extraction_price()
    model_usd = round(input_tokens * in_price / 1e6 + output_tokens * out_price / 1e6, 4)
    cap = round(cap_usd, 2) if enrich else None
    return {
        "documents": int(docs),
        "passages": len(rows),
        "model_calls": len(batches),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model,
        "model_usd": model_usd,
        "embedding_usd": 0.0,
        "enrichment": enrich,
        "enrichment_cap_usd": cap,
        "total_ceiling_usd": round(model_usd + (cap or 0.0), 4),
        "note": (
            "Spreadsheet and CRM rows are read without a model. Public enrichment spends at most"
            " its cap, and its plan is shown for approval before any paid source is used."
            if enrich
            else "Spreadsheet and CRM rows are read without a model. No paid data source is used."
        ),
    }
