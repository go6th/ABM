"""Nothing blocks the event loop (ADR-0130). SYNTHETIC data only.

The worker was killed by its liveness probe at least four times by a blocked loop (a YAML parse
per table cell, a certificate fetch, an archive zip, a ledger parse): one slow callback stalls
every request and heartbeat of the instance. ``src/obs/stalldump.py`` diagnoses a stall; this test
prevents one. It runs one hermetic company research end to end in-process (the hermetic model and
providers, the real runner, ledger, grading, export builder and pre-delivery gate: the offline
stack of ``test_offline_end_to_end``), after the service's startup warm-up
(``app.warm.warm_caches``), with asyncio debug mode on and ``loop.slow_callback_duration`` set to
the threshold.

What fails: a callback that spent more than the threshold of the LOOP THREAD's own CPU time
(``time.thread_time`` around every ``Handle._run``). That is work done on the loop: a parse, a
render, a scan, a hash that belongs in a thread or at startup.

What is excluded, and why: a callback that took longer than the threshold in wall time but not in
loop-thread CPU did not block the loop, it was starved of the GIL by work already offloaded to a
thread (the export builder's render, the name scan over the built files: one long regex or zlib
call in C keeps the GIL past the 5 ms switch interval). It is reported in the message, and it
fails only above ``EVENT_LOOP_STARVED_S`` (default 1 s), a gross stall either way. Sync I/O wait
on the loop (a blocking socket or file read) uses little CPU, so it shows as starvation here; the
audit (``docs/audits/event-loop.md``) lists those sites and ``obs.stalldump`` names them in
production.

Also excluded (``EXCLUDED``, with the reason beside each): code that is not the product's on a hot
path, today only FastAPI's one-time route setup on the test application's first request. Garbage
collection is measured (``gc.callbacks``) and subtracted: a collection runs inside whichever
callback allocated last (up to ~0.13 s here); the message reports it.

The thresholds are ``EVENT_LOOP_SLOW_CALLBACK_S`` (default 0.05 s, the audit's bar) and
``EVENT_LOOP_STARVED_S``. A failure names the code: a sampler thread records, while the loop is
late, "the async function -> the innermost ``src/`` frame".

GUARANTEE(test_a_hermetic_research_never_blocks_the_loop)
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import os
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import credits as CR
from account_intelligence.replica import jobs
from account_intelligence.runs import lifecycle
from tests.connectors.conftest import Harness
from tests.system.test_offline_end_to_end import offline

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
THRESHOLD_S = float(os.environ.get("EVENT_LOOP_SLOW_CALLBACK_S", "0.05"))
STARVED_S = float(os.environ.get("EVENT_LOOP_STARVED_S", "1.0"))
# asyncio debug mode (on by default): it logs slow callbacks itself, and it records where every
# task was created, which makes a wide fan-out's task creation cost more on the loop than it does
# in production (PYTHONASYNCIODEBUG is off there): EVENT_LOOP_ASYNCIO_DEBUG=0 measures without it
ASYNCIO_DEBUG = os.environ.get("EVENT_LOOP_ASYNCIO_DEBUG", "1") == "1"
# a blocking callback whose every sampled frame contains one of these is not the product's
# code on a hot path; each with its reason
EXCLUDED = {
    # FastAPI 0.142 builds a route's dependants and response fields on the first request that
    # reaches it: ~90 ms once per application object and route. The hermetic stack builds a new
    # application for the test, so its first request pays it here; a service pays it once per
    # process, at its first request
    "library: routing.py ": "FastAPI's lazy route setup, once per application and route",
    # asyncio debug mode records where every task was created and reads the source lines for it
    # (asyncio.format_helpers.extract_stack -> linecache); production runs without debug mode
    "linecache.py": "asyncio debug mode's own bookkeeping",
    # the sampler can catch the loop thread at the edge of a callback, in asyncio's own wake-up
    # write; that frame is not work, and the lazy route setup around it is excluded above
    "_write_to_self": "asyncio's own self-pipe wake-up write",
}


@pytest.fixture(autouse=True)
def _unsigned_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_TASKS", "1")


SRC = str(Path(__file__).resolve().parents[2] / "src")


class _Sampler:
    """Names the code that blocks: a loop task beats every few milliseconds; a thread samples the
    loop thread's stack whenever the beat is late by more than half the threshold and records the
    innermost frame under ``src/`` (the slow-callback log names only the task)."""

    def __init__(self, threshold_s: float) -> None:
        self.late_s = threshold_s / 2
        self.beat = time.monotonic()
        self.where: Counter[str] = Counter()
        self.samples: list[tuple[float, str]] = []  # (perf_counter, where) while late
        self.stop = threading.Event()
        self.thread_id = threading.get_ident()

    async def tick(self) -> None:
        # LOOP(sampler beat): cap=the test's run; exit=cancelled by the test
        while True:
            self.beat = time.monotonic()
            await asyncio.sleep(0.005)

    def watch(self) -> None:
        # LOOP(sampler watch): cap=the test's run; exit=stop event set by the test
        while not self.stop.wait(0.01):
            if time.monotonic() - self.beat <= self.late_s:
                continue
            frame = sys._current_frames().get(self.thread_id)
            if frame is None:
                continue
            # the async function the blocking call was made from (the innermost coroutine frame
            # under src/), then the innermost src/ frame: "where it was called -> what blocked"
            caller = frame
            while caller is not None and not (
                caller.f_code.co_flags & inspect.CO_COROUTINE
                and caller.f_code.co_filename.startswith(SRC)
            ):
                caller = caller.f_back
            # frame objects only (never traceback.extract_stack: it reads source lines under the
            # GIL and stalled the loop it was measuring)
            last = frame
            while last is not None and not last.f_code.co_filename.startswith(SRC):
                last = last.f_back
            if last is None:
                # library code with no src/ frame on the stack: name the innermost frame and
                # the innermost coroutine, wherever they live
                outer = frame
                while outer is not None and not outer.f_code.co_flags & inspect.CO_COROUTINE:
                    outer = outer.f_back
                lib = (
                    f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno} {frame.f_code.co_name}"
                )
                if outer is not None:
                    lib = f"{Path(outer.f_code.co_filename).name} {outer.f_code.co_name} -> {lib}"
                self.samples.append((time.perf_counter(), "library: " + lib))
            if last is not None:
                head = ""
                if caller is not None:
                    path = caller.f_code.co_filename[len(SRC) + 1 :]
                    head = f"{path}:{caller.f_lineno} {caller.f_code.co_name} -> "
                code = last.f_code
                tail = f"{code.co_filename[len(SRC) + 1 :]}:{last.f_lineno} {code.co_name}"
                self.where[head + tail] += 1
                self.samples.append((time.perf_counter(), head + tail))

    def during(self, start: float, end: float) -> list[str]:
        """What was sampled between two instants (the code of one blocking callback)."""
        return sorted({w for t, w in self.samples if start <= t <= end})


class _Timed:
    """Every callback the loop runs, timed in wall time and in the loop thread's CPU time."""

    def __init__(self) -> None:
        self.real = asyncio.events.Handle._run
        # the garbage collector's time on the loop thread (gc.callbacks): a collection runs inside
        # whichever callback allocated last, so it is subtracted from that callback's CPU and
        # reported on its own
        self.gc_s = 0.0
        self.gc_longest = 0.0
        self._gc_start = 0.0
        self._loop_thread = threading.get_ident()
        # (cpu, wall, callback, start): the callback's loop-thread CPU and wall seconds
        self.blocking: list[tuple[float, float, str, float]] = []
        self.starved: list[tuple[float, float, str, float]] = []

    def _gc(self, phase: str, info: dict[str, int]) -> None:
        if threading.get_ident() != self._loop_thread:
            return
        if phase == "start":
            self._gc_start = time.thread_time()
            return
        took = time.thread_time() - self._gc_start
        self.gc_s += took
        self.gc_longest = max(self.gc_longest, took)

    def install(self) -> None:
        real, timed = self.real, self
        gc.callbacks.append(self._gc)

        def run(handle: asyncio.Handle) -> None:
            start, cpu, collected = time.perf_counter(), time.thread_time(), timed.gc_s
            try:
                real(handle)
            finally:
                cpu = time.thread_time() - cpu - (timed.gc_s - collected)
                wall = time.perf_counter() - start
                if wall > THRESHOLD_S:
                    what = repr(handle)[:120]
                    (timed.blocking if cpu > THRESHOLD_S else timed.starved).append(
                        (round(cpu, 3), round(wall, 3), what, start)
                    )

        asyncio.events.Handle._run = run  # type: ignore[method-assign]

    def remove(self) -> None:
        asyncio.events.Handle._run = self.real  # type: ignore[method-assign]
        gc.callbacks.remove(self._gc)


def _excluded(code: list[str]) -> bool:
    return all(any(part in w for part in EXCLUDED) for w in code)


async def test_a_hermetic_research_never_blocks_the_loop(harness: Harness) -> None:
    stack = offline(harness)
    a = await stack.client("event-loop")
    container = stack.plane.app.state.container
    async with tenant_txn(a.tenant) as s:
        account, _ = await lifecycle.add_account(s, a.tenant, "acme.example", added_by="u1")
        job, _ = await CR.reserve(
            s,
            account_id=account,
            kind="new_research",
            scope="full",
            credits=200,
            actor="u1",
            now=datetime.now(UTC),
        )
    # the service's startup warm-up (api.main lifespan): first calls of cached loaders are made
    # there, in a thread, not inside the first research
    from account_intelligence.app.warm import warm_caches

    assert await asyncio.to_thread(warm_caches) == []
    loop = asyncio.get_running_loop()
    timed = _Timed()
    timed.install()
    debug, slow = loop.get_debug(), loop.slow_callback_duration
    loop.set_debug(ASYNCIO_DEBUG)
    loop.slow_callback_duration = THRESHOLD_S
    sampler = _Sampler(THRESHOLD_S)
    ticking = asyncio.create_task(sampler.tick())
    watcher = threading.Thread(target=sampler.watch, daemon=True)
    watcher.start()
    try:
        assert await jobs.run_job(a.tenant, job, jobs.GraphEngine(container)) == "started"
        from tests.system.hermetic_stack import admin

        (run,) = admin("SELECT run_id FROM research_jobs WHERE id = %s", job)[0]
        await stack.drain(a, str(run))
    finally:
        sampler.stop.set()
        ticking.cancel()
        loop.set_debug(debug)
        loop.slow_callback_duration = slow
        timed.remove()
    await stack.close(a)
    where = [f"{w} x{n}" for w, n in sampler.where.most_common(40)]
    # each callback with the code sampled while it ran (async function -> innermost src/ frame)
    blocking = [
        (cpu, wall, code)
        for cpu, wall, what, start in sorted(timed.blocking, reverse=True)
        if not _excluded(code := sampler.during(start, start + wall) or [what])
    ]
    starved = [
        (cpu, wall, sampler.during(start, start + wall) or [what])
        for cpu, wall, what, start in sorted(timed.starved, key=lambda t: t[1], reverse=True)
    ]
    assert not blocking, (
        f"{len(blocking)} callbacks did more than {THRESHOLD_S}s of work on the loop thread"
        f" (cpu s without garbage collection, wall s, code): {blocking}; garbage collection:"
        f" {timed.gc_s:.2f}s in all, longest {timed.gc_longest:.3f}s"
    )
    assert not starved or starved[0][1] <= STARVED_S, (
        f"the loop was starved for {starved[0][1]}s (cpu s, wall s, code): {starved[:10]};"
        f" sampled overall: {where}"
    )
