"""What a research about to start cannot do, told before the user pays for it.

Staging 2026-11-05: three research runs on one company ended PARTIAL because the workspace had not
granted the people data consent. Each was paid in full and the people rows stayed empty every
time. The estimate screens now name such a gap, with where to fix it, before Proceed. The run is
not refused: the rest of the research is still worth having, and the row says why it is empty.

The checks are the ones the research graph applies (``agents.guards.consent_decision``), from the
same consent store, so the screen and the run never disagree.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from obs.logging import logger

_log = logger(__name__)


async def research_gaps(tenant_id: UUID) -> list[dict[str, Any]]:
    """The gaps a research started now would have: ``{code, text, fix_path}`` per gap."""
    from agents.adapters import PgConsentStore
    from agents.config import skill_config
    from agents.guards import consent_decision

    cfg = skill_config()
    try:
        record = await PgConsentStore().current(tenant_id, cfg.consent_scope)
    except Exception:  # noqa: BLE001 - the warning never blocks the estimate screens
        _log.warning("research.gaps_unavailable", extra={"where": "consent"})
        return []
    gaps: list[dict[str, Any]] = []
    if cfg.consent_text_version is None:
        return gaps  # no approved consent text: nothing an admin could grant on the Team page
    if consent_decision(record, cfg.consent_scope, cfg.consent_text_version) is not None:
        gaps.append(
            {
                "code": "consent_required",
                "text": "People data consent is not granted, so the people lists will be left"
                " empty. An admin can grant it on the Teammates page before you start.",
                "fix_path": "/team",
            }
        )
    return gaps
