"""Source registry lookup (D-15 proposal) and lineage grouping (G11, D-11 extension).

* Account domain, its subdomains and run-derived primary hosts: T1, one lineage group.
* Configured domains: their tier, aggregator flag and lineage group.
* Broker records and feed items: by provider code (never a vendor name). When the record
  states its upstream (``lineage_hint``), the upstream host is the lineage group.
* Anything else: unlisted, lowest configured tier, own group, lineage unknown, flagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from account_intelligence.grading.config import GradingConfig, Tier
from account_intelligence.grading.ports import RunInfo, RunSource


@dataclass(frozen=True)
class SourceProfile:
    tier: Tier
    is_aggregator: bool
    lineage_group: str
    lineage_known: bool
    live_state: bool
    flags: tuple[str, ...] = ()


def _host(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    return host or None


def _under(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_primary_host(host: str, run: RunInfo) -> bool:
    return _under(host, run.account_domain) or host in run.primary_hosts


def profile_for(source: RunSource, run: RunInfo, cfg: GradingConfig) -> SourceProfile:
    reg = cfg.registry
    if source.origin in ("broker_record", "feed_item"):
        entry = reg.provider_codes.get(source.provider_code)
        hint = _host(source.lineage_hint)
        if entry is None:
            return SourceProfile(
                reg.unlisted_tier,
                reg.unlisted_is_aggregator,
                f"provider:{source.provider_code}",
                False,
                False,
                (reg.unlisted_flag,),
            )
        flags = ("aggregator",) if entry.is_aggregator else ()
        if hint is not None:
            return SourceProfile(
                entry.tier, entry.is_aggregator, f"host:{hint}", True, False, flags
            )
        return SourceProfile(
            entry.tier, entry.is_aggregator, entry.lineage_group, entry.lineage_known, False, flags
        )
    host = source.host.lower().rstrip(".")
    if is_primary_host(host, run):
        return SourceProfile(reg.account_tier, False, reg.account_lineage_group, True, True)
    for dom in reg.domains:
        if _under(host, dom.domain):
            flags = ("aggregator",) if dom.is_aggregator else ()
            return SourceProfile(
                dom.tier,
                dom.is_aggregator,
                dom.lineage_group,
                dom.lineage_known,
                dom.live_state,
                flags,
            )
    return SourceProfile(
        reg.unlisted_tier,
        reg.unlisted_is_aggregator,
        f"host:{host}",
        False,
        False,
        (reg.unlisted_flag,),
    )
