"""A stalled event loop names its blocker.

The worker was shut down by its liveness probe four times on 2026-10-28 (12:12, 14:00, 15:44 and
18:32 UTC, each about 25 minutes into the same stage) with the process idle, and the logs could
not say what was blocking. The loop task here beats once a second; a plain watchdog thread
notices when the beat is late by ``STALL_S`` and writes the main thread's stack to stderr (Cloud
Logging ``textPayload``), once per stall and again every ``STALL_S`` while it lasts. The stack
holds function names and file lines, never values.

The first version used ``faulthandler.dump_traceback_later``; its C watchdog walks the frames of
running threads without the interpreter lock and the worker died on signal 11 fifteen seconds
after its first dump (18:32:36 UTC). This one reads ``sys._current_frames`` under the lock, so it
cannot corrupt anything; it needs the lock, which a stall in pure Python or in a C call that
releases it (I/O, sleeps) still grants. A C call that holds the lock throughout goes unreported.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from typing import TextIO

STALL_S = 15.0
BEAT_S = 1.0
_MAX_FRAMES = 60


class _Watchdog(threading.Thread):
    """Daemon thread: dumps the loop thread's stack while the beat is late."""

    def __init__(self, loop_thread_id: int, out: TextIO) -> None:
        super().__init__(name="stall-watchdog", daemon=True)
        self.loop_thread_id = loop_thread_id
        self.out = out
        self.last_beat = time.monotonic()
        self.stop = threading.Event()

    def beat(self) -> None:
        self.last_beat = time.monotonic()

    def run(self) -> None:
        # LOOP(stall watchdog): cap=the process lifetime; exit=stop set at shutdown
        while not self.stop.wait(BEAT_S):
            late = time.monotonic() - self.last_beat
            if late < STALL_S:
                continue
            frame = sys._current_frames().get(self.loop_thread_id)
            lines = traceback.format_stack(frame, limit=_MAX_FRAMES) if frame else ["<no frame>\n"]
            text = (
                f"Stall dump: event loop blocked for {late:.0f} s, main thread, most recent call "
                "first:\n" + "".join(reversed(lines))
            )
            try:
                self.out.write(text)  # stderr is the log sink
                self.out.flush()
            except (OSError, ValueError):
                return
            self.last_beat = time.monotonic()  # one dump per STALL_S while the stall lasts


async def stall_dump_loop(dog: _Watchdog) -> None:
    """Beat once a second for as long as the loop runs."""
    try:
        # LOOP(stall beat): cap=the process lifetime; exit=cancelled at shutdown
        while True:
            dog.beat()
            await asyncio.sleep(BEAT_S)
    finally:
        dog.stop.set()


def install() -> asyncio.Task[None] | None:
    """Start the watchdog and the beat (None when there is no stderr to write the dump to)."""
    if sys.stderr is None:
        return None
    dog = _Watchdog(threading.get_ident(), sys.stderr)
    dog.start()
    return asyncio.create_task(stall_dump_loop(dog))
