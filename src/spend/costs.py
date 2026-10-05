"""The cost table for proposals, read from ``config/providers.yaml`` (the connector layer's
sourced prices; nothing is retyped here).

Per call, in micro-dollars:

* ``usd_per_call``: low = expected = high = the documented price;
* ``usd_per_call_by_mode``: low = the cheapest mode, expected = high = the dearest (the mode is
  chosen per call);
* credit-priced models (``credits_per_document``, ``requests_per_page``,
  ``provider_reported_credits``): no dollar rate is known (V7), so a call is held at the
  provider's unknown-cost reserve per page; low 0 (a call may return no document),
  high = reserve x the pagination page cap for paged models;
* ``unknown``: no figure (``known`` False): priced only through a sample line (K5).

The providers file stores numbers as YAML floats; each is converted through its decimal text
(``str``), never through float arithmetic.
"""

from __future__ import annotations

from decimal import Decimal

from account_intelligence.connectors.settings import ProvidersConfig, load_providers_config
from spend.models import OpCost
from spend.money import usd_to_micros

PAGED = frozenset({"requests_per_page", "provider_reported_credits"})


def _micros(value: float) -> int:
    return usd_to_micros(Decimal(str(value)))


def cost_table(config: ProvidersConfig | None = None) -> dict[tuple[str, str], OpCost]:
    """(provider code, operation) to per-call cost bounds."""
    cfg = config or load_providers_config()
    pages = cfg.defaults.pagination.max_pages
    table: dict[tuple[str, str], OpCost] = {}
    for code, provider in cfg.providers.items():
        reserve = _micros(
            provider.unknown_cost_reserve_usd or cfg.defaults.unknown_cost_reserve_usd
        )
        for op, spec in provider.operations.items():
            key = (str(code.value if hasattr(code, "value") else code), str(op.value))
            model = spec.cost_model
            if model == "usd_per_call" and spec.usd is not None:
                usd = _micros(spec.usd)
                table[key] = OpCost(
                    provider_code=key[0],
                    operation=key[1],
                    low_micros=usd,
                    expected_micros=usd,
                    high_micros=usd,
                    known=True,
                    cost_source="documented",
                )
            elif model == "usd_per_call_by_mode" and spec.usd_by_mode:
                prices = sorted(_micros(v) for v in spec.usd_by_mode.values())
                table[key] = OpCost(
                    provider_code=key[0],
                    operation=key[1],
                    low_micros=prices[0],
                    expected_micros=prices[-1],
                    high_micros=prices[-1],
                    known=True,
                    cost_source="documented",
                )
            elif model == "unknown":
                table[key] = OpCost(
                    provider_code=key[0],
                    operation=key[1],
                    low_micros=0,
                    expected_micros=0,
                    high_micros=0,
                    known=False,
                    cost_source="unknown",
                )
            else:
                high = reserve * (pages if model in PAGED else 1)
                credits = Decimal(str(spec.credits)) if spec.credits is not None else None
                table[key] = OpCost(
                    provider_code=key[0],
                    operation=key[1],
                    low_micros=0,
                    expected_micros=reserve,
                    high_micros=high,
                    known=True,
                    cost_source="credits_only",
                    unit_credits=credits,
                )
    return table
