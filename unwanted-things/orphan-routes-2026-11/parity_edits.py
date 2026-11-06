"""Parked 2026-11-03 (Prompt F, ADR-0136): POST /v1/accounts/{domain}/parity/edits and its
request models. No web screen called it (docs/audits/orphan-routes.md). parity.service
ParityService.edit stays in src/ (tests/parity/test_guards_db.py covers it). Original homes:
src/account_intelligence/api/routes/parity.py, src/account_intelligence/api/contract.py."""


# ---- from src/account_intelligence/api/routes/parity.py ----


@router.post("/{domain}/parity/edits", response_model=C.ParityVersionInfo, status_code=201)
async def parity_edit(
    domain: str, edit: C.ParityEdit, request: Request, ident: Identity = Depends(authorize)
) -> Any:
    """Apply analyst corrections (priority, lane, note by person) to a built version: the
    corrections are recorded as overrides, every number is recomputed and the files are
    rendered again as the account's next version (built or failed)."""
    found = await _found(request, ident, domain, edit.version)
    service = _service(request)
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        await audit_action(
            s,
            request,
            audit_class="run",
            action="parity_edit",
            resource_type="account",
            resource_id=account_id,
        )
    try:
        created = await service.edit(
            ident.tenant_id, found, [o.model_dump(exclude_none=True) for o in edit.overrides]
        )
    except KeyError as unknown:
        raise APIProblem(
            422,
            "unknown_person",
            "No such person on the list",
            "Name a person as the People sheet prints them.",
            extra={"name": str(unknown)[:200]},
        ) from None
    except ParityStale:
        raise APIProblem(
            409,
            "package_stale",
            "The workspace's exclusions changed",
            "The exclusion list changed after this version was built; refresh the research.",
        ) from None
    except ParityUnavailable as missing:
        raise APIProblem(
            503,
            "parity_unavailable",
            "The file renderer is not available",
            f"This server cannot render the files ({missing}).",
        ) from None
    return _info(created)


# ---- from src/account_intelligence/api/contract.py ----


class ParityOverride(BaseModel):
    """One analyst correction to a person: by name; priority, lane or note."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    priority: Literal["A", "B", "C"] | None = None
    lane: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class ParityEdit(BaseModel):
    """An edit request: the version it corrects (the newest built one when absent) and the
    corrections; the reply is the new version."""

    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)
    overrides: list[ParityOverride] = Field(min_length=1, max_length=200)
