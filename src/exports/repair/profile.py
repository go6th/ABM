"""The workspace profile: everything the repair stage needs to know about ONE workspace, read from
that workspace's own data (the account row, its stored entity lock, the skill scope, the tenant's
people consent and the user's mail and drive connection). Nothing here names an account: a new
workspace gets its own profile the day its research starts.

``profile_from_parts`` is pure (tests, the offline repair of an existing export); ``load_profile``
reads the parts from the database."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from exports.repair.core import EntityRule, WorkspaceProfile
from exports.repair.keys import host_of, registrable

# a stored connection in one of these states is not a working connection
_DEAD_CONNECTION = ("revoked", "broken", "disconnected", "expired", "error", "failed")
_PEOPLE_SCOPE = "people_data"
_LEGAL_FORMS = re.compile(
    r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|group|holdings?"
    r"|sa|ag|nv|se|gmbh)\b\.?",
    re.IGNORECASE,
)


def short_name(name: str, domain: str = "") -> str:
    """The name the account's own text uses: the name without its legal form, else the brand
    label of its domain (``Acme Holdings, Inc.`` is ``Acme``)."""
    cleaned = _LEGAL_FORMS.sub("", name or "").strip(" ,.")
    cleaned = re.sub(r"[\s,]+(and|&)$", "", cleaned, flags=re.IGNORECASE).strip(" ,.")
    if cleaned:
        return cleaned
    return (registrable(host_of(domain) or domain).split(".")[0] or name or "Account").title()


def window_start(run_date: date, months: int) -> date:
    """The first day of a window of ``months`` months ending on ``run_date``: the same day
    ``months`` earlier, both ends inside (19 Oct 2026 and 12 months is 19 Oct 2025)."""
    year, month = divmod(run_date.year * 12 + run_date.month - 1 - months, 12)
    month += 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - date(year, month, 1)).days
    return date(year, month, min(run_date.day, last))


def _rules_from_watch(raw: Any) -> list[EntityRule]:
    """Entity rules an owner entered for the workspace (``watch_config.entities``): a list of
    ``{label, patterns, counts_as_account, before}``. A malformed entry is ignored."""
    out: list[EntityRule] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or not str(item.get("label") or "").strip():
            continue
        patterns = tuple(str(p) for p in item.get("patterns") or () if str(p).strip())
        before = str(item["before"]) if item.get("before") else None
        out.append(
            EntityRule(
                label=str(item["label"]).strip(),
                patterns=patterns,
                counts_as_account=bool(item.get("counts_as_account", False)),
                before=before,
            )
        )
    return out


def profile_from_parts(
    *,
    name: str,
    domain: str,
    run_date: date,
    lock: Mapping[str, Any] | None = None,
    watch_config: Mapping[str, Any] | None = None,
    window_months: int = 12,
    lanes: Sequence[str] = (),
    title_exclusions: Sequence[str] = (),
    out_of_scope: Sequence[str] = (),
    people_consent: bool = False,
    mail_drive_connected: bool = False,
    platforms: Any = None,
) -> WorkspaceProfile:
    """Build the profile from its parts (see the module docstring). The account's domains are
    its own domain, the registrable domains of its entity lock (redirects, subsidiaries) and the
    owner's ``watch_config.domains``; careers hosts that sit on a third-party job board are kept
    apart (``careers_hosts``) and never make that board the account's domain."""
    lock = lock or {}
    watch = watch_config or {}
    short = str(watch.get("short_name") or "").strip() or short_name(name, domain)
    primary = registrable(host_of(domain) or domain)
    domains = {d for d in (primary, *(str(x) for x in lock.get("allowed_domains") or ())) if d}
    domains |= {registrable(str(x)) for x in watch.get("domains") or () if str(x).strip()}
    careers = {str(h).lower() for h in lock.get("careers_hosts") or () if h}
    careers_url = str(watch.get("careers_url") or "")
    if careers_url:
        careers.add(host_of(careers_url))
    aliases = {
        a
        for a in (
            short,
            short_name(name, domain),
            name,
            str(lock.get("brand") or ""),
            str(lock.get("legal_name") or ""),
        )
        if a
    }
    aliases |= {str(a) for a in watch.get("aliases") or () if str(a).strip()}
    rules: list[EntityRule] = []
    for sub in lock.get("subsidiaries") or ():
        label = str(sub.get("name") or "").strip() if isinstance(sub, Mapping) else ""
        if label:
            rules.append(
                EntityRule(label=label, patterns=(re.escape(label),), counts_as_account=True)
            )
            sub_domain = str(sub.get("domain") or "") if isinstance(sub, Mapping) else ""
            if sub_domain:
                domains.add(registrable(sub_domain))
    rules += _rules_from_watch(watch.get("entities"))
    default_platforms = platforms
    if default_platforms is None:
        from parity.platforms import default_platforms as load

        default_platforms = load()
    lane_names = tuple(lanes) or tuple(lane.name for lane in default_platforms.lanes)
    return WorkspaceProfile(
        account_name=name,
        account_short=short,
        domain=primary,
        run_date=run_date,
        window_from=window_start(run_date, window_months),
        window_to=run_date,
        domains=frozenset(domains),
        aliases=tuple(sorted(aliases, key=str.lower)),
        account_label=short,
        entities=tuple(rules),
        lanes=lane_names,
        title_exclusions=tuple(title_exclusions),
        out_of_scope_platforms=tuple(out_of_scope),
        people_consent=people_consent,
        mail_drive_connected=mail_drive_connected,
        careers_hosts=frozenset(careers),
        platforms=default_platforms,
    )


async def load_profile(
    s: Any, account_id: UUID, user_id: str, now: datetime, scope: Any
) -> WorkspaceProfile:
    """The profile of one account for one user, read from the database. ``scope`` is the skill
    scope (``agents.config.Scope``: window months, title exclusions, platforms never reported);
    the caller loads it, because ``exports`` sits below ``agents`` and may not import it."""
    head = (
        await s.execute(
            text(
                "SELECT a.name, a.domain, to_jsonb(a) AS row,"
                " (SELECT coalesce(r.finished_at, r.created_at) FROM account_runs r"
                "  WHERE r.account_id = a.id AND r.status IN ('COMPLETED', 'PARTIAL',"
                "  'BUDGET_EXCEEDED') ORDER BY r.finished_at DESC NULLS LAST LIMIT 1) AS ran"
                " FROM accounts a WHERE a.id = :a"
            ),
            {"a": account_id},
        )
    ).one()
    row = dict(head.row or {})
    consent = await s.scalar(
        text(
            "SELECT revoked_at IS NULL FROM people_data_consents WHERE :p = ANY(scopes)"
            " ORDER BY id DESC LIMIT 1"
        ),
        {"p": _PEOPLE_SCOPE},
    )
    mail = await s.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM connector_connections c WHERE c.owner_user_id = :u"
            " AND c.status <> ALL(:dead))"
        ),
        {"u": user_id, "dead": list(_DEAD_CONNECTION)},
    )
    run_date = (head.ran or now).date()
    return profile_from_parts(
        name=str(head.name or head.domain),
        domain=str(head.domain),
        run_date=run_date,
        lock=row.get("entity_lock") or None,
        watch_config={
            **(row.get("watch_config_json") or {}),
            "careers_url": row.get("careers_url"),
        },
        window_months=int(dict(scope.window_months).get("postings", 12)),
        lanes=tuple(row.get("lanes") or ()),
        title_exclusions=tuple(scope.excluded_title_patterns),
        out_of_scope=tuple(scope.never_report),
        people_consent=bool(consent),
        mail_drive_connected=bool(mail),
    )
