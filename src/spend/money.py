"""Integer money (K6). Pure; no float anywhere.

``Micro`` is USD in integer micro-dollars (1 USD = 1,000,000). Parsing accepts ``Decimal``,
``int`` or a decimal string and refuses ``float``, NaN, infinities and negatives where a cost is
meant. Costs round UP to the next micro-dollar (a cost is never under-counted). Credits are
``Decimal`` in their own type and convert to USD only through a sourced rate.

GUARANTEE(test_money_rejects_float_nan_and_negative): no float, NaN or infinity becomes money.
GUARANTEE(test_cost_rounding_is_up_and_monotone): rounding a cost never lowers it.
GUARANTEE(test_parts_sum_to_total_exactly): integer sums are exact.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_CEILING, Decimal, InvalidOperation

MICROS_PER_USD = 1_000_000
_QUANTUM = Decimal("0.000001")


class MoneyError(ValueError):
    """A value that is not valid money (float, NaN, infinity, negative cost, bad text)."""


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool | float):
        raise MoneyError("float is not money")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"not a decimal: {value!r}") from exc
    else:
        raise MoneyError(f"unsupported money type {type(value).__name__}")
    if not number.is_finite():
        raise MoneyError("money must be finite")
    return number


def usd_to_micros(value: object, *, allow_negative: bool = False) -> int:
    """USD (Decimal, int or decimal string) to micro-dollars, rounding UP (toward +infinity)."""
    number = _decimal(value)
    if number < 0 and not allow_negative:
        raise MoneyError("a cost cannot be negative")
    return int((number * MICROS_PER_USD).to_integral_value(rounding=ROUND_CEILING))


def micros_to_usd(micros: int) -> Decimal:
    """Micro-dollars to a 6-decimal ``Decimal`` (exact)."""
    if isinstance(micros, bool) or not isinstance(micros, int):
        raise MoneyError("micros must be an int")
    return (Decimal(micros) / MICROS_PER_USD).quantize(_QUANTUM)


def round_up_to_step(micros: int, step_micros: int) -> int:
    """Round a non-negative amount UP to a multiple of ``step_micros`` (> 0)."""
    if step_micros <= 0:
        raise MoneyError("step must be positive")
    if micros < 0:
        raise MoneyError("amount must not be negative")
    return -(-micros // step_micros) * step_micros


def total(parts: Iterable[int]) -> int:
    """Exact sum of micro-dollar parts."""
    out = 0
    for part in parts:
        if isinstance(part, bool) or not isinstance(part, int):
            raise MoneyError("parts must be int micros")
        out += part
    return out


def credits(value: object) -> Decimal:
    """Provider credits as a non-negative ``Decimal``."""
    number = _decimal(value)
    if number < 0:
        raise MoneyError("credits cannot be negative")
    return number


def credits_to_micros(amount: Decimal, usd_per_credit: Decimal | None) -> int | None:
    """Credits to micro-dollars through a sourced rate; ``None`` when no rate exists (never 0)."""
    if usd_per_credit is None:
        return None
    return usd_to_micros(credits(amount) * credits(usd_per_credit))


def format_usd(micros: int) -> str:
    """``$1.234567`` style text with 2 to 6 decimals (trailing zeros beyond cents removed)."""
    value = micros_to_usd(micros)
    text = f"{value:.6f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"${whole}.{frac.ljust(2, '0')}"
