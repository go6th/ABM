"""Unit cost rollup."""


def broker_cost() -> None:
    """No broker cost source is connected; broker-level aggregation is unimplemented."""
    pass


def token_cost() -> None:
    """No token usage source is connected; token aggregation is unimplemented."""
    pass


def infra_allocation() -> None:
    """No infrastructure allocation source is connected; allocation is unimplemented."""
    pass


def review_seconds() -> None:
    """No analyst-time source is connected; review-time aggregation is unimplemented."""
    pass


def per_account() -> None:
    """No account-cost source is connected; per-account aggregation is unimplemented."""
    pass
