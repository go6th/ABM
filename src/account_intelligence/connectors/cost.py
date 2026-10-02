"""Cost model: estimate before a call, actual after, from config/providers.yaml.

Credits and dollars are separate numbers (known defect 4). A dollar figure is
``None`` unless documented; the budget is then charged a conservative reserve and
the call record says ``cost_source='unknown'`` (never 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.models import (
    OperationArgs,
    PeopleByTechnologyArgs,
    WebSearchArgs,
)
from account_intelligence.connectors.settings import ProvidersConfig


@dataclass(frozen=True)
class CostFigure:
    credits: float | None  # provider units ('credit' or 'request'); None when unknown
    unit: str
    usd: float | None  # documented or derived dollars; None when unknown
    # 'documented' | 'provider_reported' (dollars known) | 'credits_only' (credits known,
    # dollar rate unknown) | 'unknown' (nothing known)
    cost_source: str
    charged_usd: float  # what the run budget is charged; never 0 for an unknown cost


class CostModel:
    def __init__(self, config: ProvidersConfig) -> None:
        self._config = config

    # VERIFY(p_scrape, p_signals, p_people, p_org): dollars per credit are plan-dependent and
    # unknown; until set in config, the budget is charged the unknown-cost reserve.
    def _reserve(self, code: ProviderCode) -> float:
        own = self._config.provider(code).unknown_cost_reserve_usd
        return own if own is not None else self._config.defaults.unknown_cost_reserve_usd

    def _figure(
        self, code: ProviderCode, credits: float | None, usd: float | None, source: str, unit: str
    ) -> CostFigure:
        if usd is None and credits is not None:
            rate = self._config.provider(code).usd_per_credit
            usd = credits * rate if rate is not None else None
        if usd is not None:
            return CostFigure(credits, unit, usd, source, usd)
        if credits == 0:
            # Documented zero for this outcome (e.g. no document returned): nothing charged.
            return CostFigure(0.0, unit, 0.0, source, 0.0)
        # Credits known but no documented dollar rate: 'credits_only'. Nothing known: 'unknown'.
        return CostFigure(
            credits,
            unit,
            None,
            "unknown" if credits is None else "credits_only",
            self._reserve(code),
        )

    def estimate(self, code: ProviderCode, op: Operation, args: OperationArgs) -> CostFigure:
        """Upper estimate for one page/request, reserved before the call."""
        spec = self._config.op_cost(code, op)
        provider = self._config.provider(code)
        match spec.cost_model:
            case "usd_per_call_by_mode":
                assert isinstance(args, WebSearchArgs) and spec.usd_by_mode is not None
                extra = max(0, args.max_results - (spec.included_results or 0))
                usd = spec.usd_by_mode[args.mode] + extra * (spec.usd_per_additional_result or 0.0)
                return self._figure(code, None, usd, "documented", "usd")
            case "usd_per_call":
                return self._figure(code, None, spec.usd, "documented", "usd")
            case "credits_per_document" | "requests_per_page":
                return self._figure(code, spec.credits, None, "documented", spec.unit)
            case "provider_reported_credits":
                credits: float | None
                if op is Operation.ORG_RESOLVE:
                    paid = [
                        a for a in provider.org_attributes if a not in provider.org_free_attributes
                    ]
                    credits = 1.0 + len(paid)
                elif op is Operation.PEOPLE_BY_TECHNOLOGY:
                    assert isinstance(args, PeopleByTechnologyArgs)
                    paid = [
                        a
                        for a in provider.people_attributes
                        if a not in provider.people_free_attributes
                    ]
                    credits = float((provider.people_page_limit or 1) * (1 + len(paid)))
                else:
                    credits = spec.estimate_credits
                return self._figure(code, credits, None, "documented", spec.unit)
            case "unknown":
                return self._figure(code, None, None, "unknown", spec.unit)
        raise AssertionError(spec.cost_model)  # pragma: no cover

    def actual(
        self,
        code: ProviderCode,
        op: Operation,
        args: OperationArgs,
        *,
        provider_credits: float | None,
        document_returned: bool,
    ) -> CostFigure:
        spec = self._config.op_cost(code, op)
        match spec.cost_model:
            case "provider_reported_credits":
                if provider_credits is None:
                    return self.estimate(code, op, args)  # response did not say: keep reserve
                return self._figure(code, provider_credits, None, "provider_reported", spec.unit)
            case "credits_per_document":
                credits = spec.credits if document_returned else 0.0
                return self._figure(code, credits, None, "documented", spec.unit)
            case _:
                return self.estimate(code, op, args)
