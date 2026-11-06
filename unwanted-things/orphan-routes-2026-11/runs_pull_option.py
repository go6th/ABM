"""Parked 2026-11-03 (Prompt F, ADR-0136): GET /v1/runs/pull-option and the SQL only it called.
No web screen called it (docs/audits/orphan-routes.md). Original homes:
src/account_intelligence/api/routes/runs.py, src/agents/adapters.py,
src/account_intelligence/api/contract.py."""


# ---- from src/account_intelligence/api/routes/runs.py ----


@router.get("/pull-option", response_model=C.PullOption, response_model_exclude_unset=True)
async def get_pull_option(
    domain: str,
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> dict[str, Any]:
    """Whether "Phase 2 only" may be offered for the account, with the pull's date and coverage
    (work item 2). Counts only; no content, provider or cost."""
    async with tenant_txn(ident.tenant_id) as s:
        option = await pull_option(s, domain.strip().lower().rstrip("."))
    return {"available": option is not None, **(option or {})}


# ---- from src/agents/adapters.py ----


async def pull_option(s: AsyncSession, account_key: str) -> dict[str, Any] | None:
    """The newest usable pull for "Phase 2 only": run id, date and coverage counts."""
    row = (
        await s.execute(
            text(
                "SELECT run_id, created_at, manifest FROM pull_manifests WHERE account_key = :a"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": account_key},
        )
    ).first()
    if row is None:
        return None
    rows = row.manifest["rows"]
    stored = sum(1 for r in rows if r["status"] == "stored")
    return {
        "pull_run_id": str(row.run_id),
        "pulled_at": row.created_at.isoformat(),
        "rows_stored": stored,
        "rows_blocked": len(rows) - stored,
    }


# ---- from src/account_intelligence/api/contract.py ----


class PullOption(Out):
    """Response body: pull option."""

    available: bool
    pull_run_id: str | None = None
    pulled_at: str | None = None
    rows_stored: int | None = None
    rows_blocked: int | None = None
