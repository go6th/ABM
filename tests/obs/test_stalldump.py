"""A blocked event loop writes the blocking frame (obs.stalldump)."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from obs import stalldump


def test_a_blocked_loop_dumps_the_blocking_frame() -> None:
    """In a child process: the beat runs, then the loop blocks in time.sleep; the watchdog
    names ``blocking_call`` on stderr although the loop thread is stuck."""
    code = textwrap.dedent(
        """
        import asyncio, time
        from obs import stalldump
        stalldump.STALL_S = 1.0
        async def main():
            stalldump.install()
            await asyncio.sleep(1.5)
            def blocking_call():
                time.sleep(3)
            blocking_call()
        asyncio.run(main())
        """
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PYTHONPATH": "src"},
        check=False,
    )
    assert done.returncode == 0, done.stderr[-500:]
    assert "blocking_call" in done.stderr and "most recent call first" in done.stderr


def test_a_pure_python_stall_is_dumped_too() -> None:
    """A stall that never releases the interpreter lock to C code (a long pure-Python loop, as
    the YAML parser was on 2026-10-28) is still seen: the watchdog gets the lock at the switch
    interval."""
    code = textwrap.dedent(
        """
        import asyncio, time
        from obs import stalldump
        stalldump.STALL_S = 1.0
        async def main():
            stalldump.install()
            await asyncio.sleep(1.5)
            def spin():
                end = time.monotonic() + 3
                n = 0
                while time.monotonic() < end:
                    n += 1
            spin()
        asyncio.run(main())
        """
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PYTHONPATH": "src"},
        check=False,
    )
    assert done.returncode == 0, done.stderr[-500:]
    assert "spin" in done.stderr


@pytest.mark.asyncio
async def test_a_running_loop_stays_quiet(capfd: pytest.CaptureFixture[str]) -> None:
    import asyncio

    task = stalldump.install()
    await asyncio.sleep(1.5)
    if task is not None:
        task.cancel()
    assert "most recent call first" not in capfd.readouterr().err
