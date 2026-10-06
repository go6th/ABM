"""Model call cost from the candidate's sourced price (R9): unknown price is ``None`` and flagged,
never zero. Decimal arithmetic, rounded to micro-dollars.

GUARANTEE(test_unknown_price_is_none_never_zero): R9.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from llm.config import Price
from llm.models import Usage

MTOK = Decimal(1_000_000)


def cost_usd(price: Price, usage: Usage) -> Decimal | None:
    """Input and output tokens at the configured prices; None when either price is unknown.
    Cache read and write prices are not sourced, so cached tokens are priced as input [VERIFY]."""
    if price.input_per_mtok is None or price.output_per_mtok is None:
        return None
    total = (
        Decimal(usage.input_tokens) * price.input_per_mtok
        + Decimal(usage.output_tokens) * price.output_per_mtok
    ) / MTOK
    return total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
