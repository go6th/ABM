"""The render checks never block the event loop and never leave a converter running
(security review 2026-10-29, F4 and F16).

* ``ExportBuilder.build_all`` runs the whole assembly (Word renderer, scans, LibreOffice
  conversions) in a worker thread: a coroutine scheduled beside it keeps running while a
  conversion is slow;
* ``LibreOffice`` starts ``soffice`` in its own process group and kills the whole group on
  timeout (``soffice`` starts ``soffice.bin``; killing the direct child alone leaves the worker),
  under memory and CPU limits, and names why a conversion produced nothing (``last_failure``)."""

from __future__ import annotations

import asyncio
import os
import stat
import time
from pathlib import Path

import pytest

from exports.verify import LibreOffice, pdf_row_presence, recalc_check
from tests.exports.corpus import account
from tests.exports.test_delivery_gates import _make_builder


class SlowConverter:
    """A converter that takes a while and then produces nothing (a hung or failed soffice)."""

    last_failure = "timeout"

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    def pdf_bytes(self, docx: bytes) -> bytes | None:
        self.calls += 1
        time.sleep(self.seconds)  # synchronous on purpose: the builder must move it off the loop
        return None

    def pdf_text(self, docx: bytes) -> str | None:
        return self.pdf_bytes(docx) and ""

    def recalculated(self, xlsx: bytes) -> bytes | None:
        self.calls += 1
        time.sleep(self.seconds)
        return None


@pytest.mark.asyncio
async def test_a_slow_conversion_does_not_block_the_event_loop() -> None:
    slow = SlowConverter(0.4)
    builder = _make_builder(account(), pdf=slow, recalc=slow)
    ticks = 0
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(heartbeat())
    started = time.perf_counter()
    await builder.build_all("t", "r")
    elapsed = time.perf_counter() - started
    stop.set()
    await task
    assert slow.calls >= 1 and elapsed >= 0.35
    # a blocked loop ticks about once per conversion; a free one ticks every 10 ms
    assert ticks >= elapsed / 0.01 * 0.4, (ticks, elapsed)


def _script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in ("Z", "X")


def test_a_timed_out_conversion_kills_the_whole_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    fake = _script(
        tmp_path / "soffice",
        f"sleep 300 &\necho $! > {pidfile}\nwait\n",
    )
    office = LibreOffice(timeout_s=1.0, binary=fake)
    started = time.perf_counter()
    assert office.pdf_bytes(b"docx") is None
    assert time.perf_counter() - started < 8  # one attempt, not two: a timeout is not re-run
    assert office.last_failure == "timeout"
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild), "the converter's child process survived the timeout"
    result = pdf_row_presence(b"docx", ["L-0001"], office)
    assert result.status == "not_run" and "timeout" in result.reason
    assert recalc_check(b"x", {}, office).status == "not_run"


def test_the_converter_runs_under_resource_limits(tmp_path: Path) -> None:
    """The converter is started in its own session with a memory and CPU limit set before it
    starts (the fake prints its limits into the output file)."""
    fake = _script(
        tmp_path / "soffice",
        'for a; do last=$a; done\nout=""\nprev=""\n'
        'for a; do [ "$prev" = "--outdir" ] && out=$a; prev=$a; done\n'
        'echo "$(ulimit -v) $(ulimit -t) $(ps -o pgid= -p $$ | tr -d " ") $$" > "$out/in.pdf"\n',
    )
    office = LibreOffice(timeout_s=10.0, binary=fake)
    body = office.pdf_bytes(b"docx")
    assert body is not None
    memory, cpu, pgid, pid = body.decode().split()
    assert int(memory) <= 6 * 1024 * 1024 and int(cpu) <= 240
    assert pgid == pid  # its own process group (a new session), not the worker's
    assert os.getpgrp() != int(pgid)
    assert office.last_failure is None
