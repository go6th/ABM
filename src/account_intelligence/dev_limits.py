"""Local stack switch that lifts the breadth and spend caps (``DEV_UNCAPPED=1``, dev only).

Production configuration keeps every cap. On the local stack a developer may want a run to read
everything a provider has: with ``APP_ENV=dev`` (the default) and ``DEV_UNCAPPED=1`` the credit
cap gate is skipped (the run keeps ``RUN_BUDGET_USD``), provider pagination, the job pipeline caps
and the technologies per lane are lifted to ``UNCAPPED``, and so is the slice ceiling: a run takes
as long as its research needs. Guards that only bound a loop (gap passes, retries, the slice length
itself) stay: they never limit what is researched.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

UNCAPPED = 1_000_000
# slice length on the uncapped local stack (production keeps the configured 240 s)
DEV_SLICE_SECONDS = 1200.0


def dev_uncapped(env: Mapping[str, str] | None = None) -> bool:
    """True on the local stack with ``DEV_UNCAPPED=1``; never in staging or production."""
    e = os.environ if env is None else env
    return e.get("APP_ENV", "dev") == "dev" and e.get("DEV_UNCAPPED") == "1"
