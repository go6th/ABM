"""The one price table: every model's prices, from ``config/models.yaml`` (``models.<id>.pricing``,
USD per million tokens). Research calls (``llm.reply_cache.price``), Ask and chat turns
(``chat.registry``, ``api.routes.ask``) and the file-mode estimate (``documents.estimate``) all
price from here; ``config/chat_models.yaml`` holds no price (``test_single_price_table``).

GUARANTEE(test_single_price_table)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

MTOK = Decimal(1_000_000)
FIELDS = (
    "input_usd_per_million",
    "cached_read_usd_per_million",
    "cache_write_usd_per_million",
    "output_usd_per_million",
)


@dataclass(frozen=True)
class Price:
    """One model's prices, USD per million tokens."""

    input: Decimal
    cached_read: Decimal
    cache_write: Decimal
    output: Decimal


def _decimal(value: Any, where: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"models.yaml: {where} must be a finite nonnegative price") from None
    if not out.is_finite() or out < 0:
        raise ValueError(f"models.yaml: {where} must be a finite nonnegative price")
    return out


@lru_cache(maxsize=2)
def prices(path: Path | None = None) -> dict[str, Price]:
    """Every priced model by id; a model with a ``pricing`` block must give all four prices."""
    import yaml

    from account_intelligence.config import CONFIG_DIR

    doc = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    out: dict[str, Price] = {}
    for model, spec in (doc.get("models") or {}).items():
        pricing = (spec or {}).get("pricing")
        if not pricing:
            continue
        missing = [f for f in FIELDS if f not in pricing]
        if missing or set(pricing) - set(FIELDS):
            raise ValueError(f"models.yaml: {model} pricing needs exactly {', '.join(FIELDS)}")
        out[str(model)] = Price(*(_decimal(pricing[f], f"{model}.{f}") for f in FIELDS))
    return out


def price_of(model: str) -> Price | None:
    """The model's prices; None when ``models.yaml`` does not price it (never zero)."""
    return prices().get(model)
