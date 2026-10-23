"""Two defects that hid the athenahealth RuntimeError (2026-10-22).

The batch pacer held a module-level asyncio.Lock: a second event loop using it raised "is bound
to a different event loop". The pacer must work across loops and still space requests. An error
must name its own code location, without its message, so it can be traced."""

from __future__ import annotations

import asyncio
import time

from account_intelligence.platform.llm import RequestPacer
from obs.scrub import error_where, scrub_exception


def test_one_pacer_serves_two_event_loops_without_error() -> None:
    pacer = RequestPacer(per_minute=60000)  # 1 ms apart: fast, still a real interval

    async def burst() -> None:
        await asyncio.gather(*(pacer.wait() for _ in range(3)))

    asyncio.run(burst())  # the first loop binds nothing
    asyncio.run(burst())  # a second loop: must not raise


def test_the_pacer_spaces_requests() -> None:
    pacer = RequestPacer(per_minute=600)  # 0.1 s apart
    start = time.monotonic()

    async def three() -> None:
        for _ in range(3):
            await pacer.wait()

    asyncio.run(three())
    assert time.monotonic() - start >= 0.19


def test_an_error_names_its_own_code_location_not_its_message() -> None:
    try:
        RequestPacer(None)  # type: ignore[arg-type]  # raises inside the product's own code
    except TypeError as error:
        where = error_where(error)
        logged = scrub_exception(error)
    assert where is not None and "platform/llm.py" in where and where.endswith(":__init__")
    assert logged["where"] == where and logged["error_class"] == "TypeError"
    assert "NoneType" not in str(logged)  # the message itself never reaches a log line
