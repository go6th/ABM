"""The parser sandbox treats the parser as untrusted (fix prompt D, task 3; ADR-0124).

On top of the kernel filter and the audit hook (``test_units.py``) the child:

* gets the document as an inherited, read-only file descriptor (fd 0), never its path;
* may read only the code and the config (Python's own reads): another tenant's document on the
  worker's bucket mount, ``/proc/<parent>/environ`` or ``/etc`` is refused;
* runs with an open-file limit beside the memory, CPU, file-size and core limits;
* leaves the network namespace where the kernel lets it, and drops root where it has it;
* reports which of these it applied, and the parent records it;
* a non-zero exit fails the parse even after the child printed units.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from documents import sandbox, sandbox_child
from documents.sandbox import SandboxFailed, parse_in_sandbox, plan_in_sandbox


def _parse(path: Path, applied: dict[str, str] | None = None) -> list[Any]:
    async def go() -> list[Any]:
        return [
            u
            async for u in parse_in_sandbox(
                str(path),
                "csv",
                path.name,
                timeout_s=60,
                memory_mb=1536,
                cpu_s=60,
                max_text_bytes=10**6,
                applied=applied,
            )
        ]

    return asyncio.run(go())


@pytest.mark.skipif(sys.platform != "linux", reason="the kernel filter exists on Linux only")
def test_the_child_reports_the_protections_it_applied(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"Name,Title\nLee Park,VP\n")
    applied: dict[str, str] = {}
    got = _parse(path, applied)
    assert got[0].rows == [(2, ["Lee Park", "VP"])]  # the record line is not a unit
    assert applied["seccomp"] == "on"
    assert applied["input"] == "fd"
    assert applied["reads"] == "confined"
    assert applied["rlimits"] == "as,cpu,fsize,core,nofile"
    assert applied["netns"] in ("on", "unavailable")
    assert applied["uid"] in ("dropped", "not_root")
    planned: dict[str, str] = {}
    parts = asyncio.run(
        plan_in_sandbox(
            str(path),
            "csv",
            path.name,
            timeout_s=60,
            memory_mb=1536,
            cpu_s=60,
            part_bytes=1 << 20,
            pages_per_part=50,
            applied=planned,
        )
    )
    assert parts and planned["input"] == "fd" and planned["seccomp"] == "on"


@pytest.mark.asyncio
async def test_the_child_gets_the_document_as_a_descriptor_not_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secret-name.csv"
    path.write_bytes(b"a\n")
    seen: dict[str, Any] = {}

    async def spawn(*args: Any, **kwargs: Any) -> Any:
        seen["args"], seen["kwargs"] = args, kwargs
        raise OSError("stop before the child runs")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(OSError, match="stop before"):
        async for _ in parse_in_sandbox(
            str(path), "csv", "a.csv", timeout_s=1, memory_mb=64, cpu_s=1, max_text_bytes=10
        ):
            pass
    assert str(path) not in seen["args"] and "-" in seen["args"]
    stdin = seen["kwargs"]["stdin"]
    assert stdin.name == str(path) and stdin.mode == "rb"
    assert stdin.closed  # the parent's copy is closed once the child has its own


def _run_child_code(code: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, env={}
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_reads_outside_the_code_and_config_are_refused(tmp_path: Path) -> None:
    other = tmp_path / "tenants" / "other" / "raw"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another tenant's document")
    code = (
        "import os, sys, documents.sandbox_child as c\n"
        "c.confine_reads(c.read_roots())\n"
        "sys.addaudithook(c._audit)\n"
        "def t(f):\n"
        "    try:\n        f(); print('ALLOWED')\n"
        "    except PermissionError:\n        print('REFUSED')\n"
        f"t(lambda: open({str(other)!r}, 'rb').read())\n"
        f"t(lambda: os.listdir({str(other.parent.parent)!r}))\n"
        "t(lambda: open('/proc/%d/environ' % os.getppid(), 'rb').read())\n"
        "t(lambda: open('/etc/hostname', 'rb').read())\n"
        "t(lambda: __import__('colorsys'))\n"  # a module not imported yet: the code is readable
        "t(lambda: open(c.__file__, 'rb').read())\n"
    )
    assert _run_child_code(code) == [
        "REFUSED",
        "REFUSED",
        "REFUSED",
        "REFUSED",
        "ALLOWED",
        "ALLOWED",
    ]


@pytest.mark.skipif(sys.platform != "linux", reason="resource limits as on the worker")
def test_the_child_limits_its_open_files() -> None:
    code = (
        "import resource, documents.sandbox_child as c\n"
        "c._limits(1536, 30)\n"
        "print(*resource.getrlimit(resource.RLIMIT_NOFILE))\n"
        "print(*resource.getrlimit(resource.RLIMIT_FSIZE))\n"
    )
    nofile = sandbox_child.OPEN_FILES
    assert _run_child_code(code) == [str(nofile), str(nofile), "0", "0"]


class _Os:
    def __init__(self, euid: int) -> None:
        self.euid, self.calls = euid, []  # type: list[tuple[str, Any]]

    def geteuid(self) -> int:
        return self.euid

    def setgroups(self, groups: list[int]) -> None:
        self.calls.append(("setgroups", groups))

    def setgid(self, gid: int) -> None:
        self.calls.append(("setgid", gid))

    def setuid(self, uid: int) -> None:
        self.calls.append(("setuid", uid))


def test_root_is_dropped_to_nobody_and_a_non_root_user_is_left_alone() -> None:
    root = _Os(0)
    assert sandbox_child.drop_root(root) == "dropped"
    assert root.calls == [("setgroups", []), ("setgid", 65534), ("setuid", 65534)]
    user = _Os(10001)
    assert sandbox_child.drop_root(user) == "not_root"
    assert user.calls == []


def test_a_non_zero_exit_fails_the_parse_after_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = json.dumps({"kind": "paragraph", "location": {}, "text": "x"})
    script = f"import sys; print({unit!r}); sys.stdout.flush(); sys.exit(3)"

    async def child(*_: Any, **__: Any) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(sandbox, "_child", child)
    path = tmp_path / "a.csv"
    path.write_bytes(b"a\n")
    with pytest.raises(SandboxFailed, match="parser_failed"):
        _parse(path)
