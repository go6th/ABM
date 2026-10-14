"""Run a document parser in the sandbox subprocess (``documents.sandbox_child``) and read its units.

The child gets an empty environment, Python's isolated mode, resource limits and an audit hook
that refuses network, processes and writes; the parent enforces a wall-clock timeout and caps the
text it accepts. A large document is planned into parts (``plan_in_sandbox``) and each part is
parsed by its own child with the same limits (``parse_in_sandbox(part=...)``). A child that
dies, times out or prints something that is not a unit fails the parse with ``SandboxFailed``
(the error class only; document text never reaches a log).

GUARANTEE(test_the_parser_sandbox_has_no_network)
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

_LINE_LIMIT = 64 * 1024 * 1024


class SandboxFailed(RuntimeError):
    """The parser child failed; ``code`` is a short reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class ParsedUnit:
    """One unit the child printed."""

    kind: str
    location: dict[str, Any]
    text: str
    heading: str | None = None
    fatal: bool = False
    findings: list[dict[str, Any]] = field(default_factory=list)
    header: list[str] | None = None
    rows: list[tuple[int, list[Any]]] | None = None
    table: str | None = None
    check: str | None = None


def _unit(line: bytes) -> ParsedUnit:
    data = json.loads(line)
    if not isinstance(data, dict):
        raise SandboxFailed("bad_output")
    rows = data.get("rows")
    return ParsedUnit(
        kind=str(data.get("kind") or "note"),
        location=dict(data.get("location") or {}),
        text=str(data.get("text") or ""),
        heading=data.get("heading"),
        fatal=bool(data.get("fatal")),
        findings=list(data.get("findings") or []),
        header=data.get("header"),
        rows=[(int(n), list(cells)) for n, cells in rows] if rows is not None else None,
        table=data.get("table"),
        check=data.get("check"),
    )


async def _child(
    path: str, kind: str, name: str, memory_mb: int, cpu_s: int, part: dict[str, Any]
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "documents.sandbox_child",
        path,
        kind,
        name[:200],
        str(memory_mb),
        str(cpu_s),
        json.dumps(part),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PYTHONHASHSEED": "0", "LANG": "C.UTF-8"},
        limit=_LINE_LIMIT,
    )


async def plan_in_sandbox(
    path: str,
    kind: str,
    name: str,
    *,
    timeout_s: float,
    memory_mb: int,
    cpu_s: int,
    part_bytes: int,
    pages_per_part: int,
) -> list[dict[str, Any]]:
    """The document's parts (``documents.parts``), planned in the sandbox: it reads the file."""
    plan = {"plan": {"part_bytes": part_bytes, "pages_per_part": pages_per_part}}
    proc = await _child(path, kind, name, memory_mb, cpu_s, plan)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        raise SandboxFailed("plan_timeout") from None
    except ValueError:
        raise SandboxFailed("bad_output") from None
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        detail = err[:200].decode("ascii", "replace").strip()
        raise SandboxFailed(f"plan_failed:{detail or proc.returncode}"[:80])
    try:
        parts = json.loads(out)["parts"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise SandboxFailed("bad_output") from None
    if not isinstance(parts, list) or not parts:
        raise SandboxFailed("bad_output")
    return [dict(p) for p in parts]


async def parse_in_sandbox(
    path: str,
    kind: str,
    name: str,
    *,
    timeout_s: float,
    memory_mb: int,
    cpu_s: int,
    max_text_bytes: int,
    part: dict[str, Any] | None = None,
) -> AsyncIterator[ParsedUnit]:
    """Units of the document at ``path``, or of one part of it (see the module docstring)."""
    proc = await _child(path, kind, name, memory_mb, cpu_s, part or {})
    assert proc.stdout is not None and proc.stderr is not None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    total = 0
    try:
        # LOOP(sandbox unit lines): cap=units the child prints within timeout_s and
        # max_text_bytes; exit=end of output, timeout, text cap
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise SandboxFailed("parse_timeout")
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), remaining)
            except TimeoutError:
                raise SandboxFailed("parse_timeout") from None
            except ValueError:
                raise SandboxFailed("unit_too_large") from None
            if not line:
                break
            try:
                unit = _unit(line)
            except (json.JSONDecodeError, TypeError, ValueError):
                raise SandboxFailed("bad_output") from None
            total += len(unit.text.encode("utf-8"))
            if total > max_text_bytes:
                raise SandboxFailed("text_limit")
            yield unit
        code = await asyncio.wait_for(proc.wait(), max(1.0, deadline - loop.time()))
        if code != 0:
            err = (await proc.stderr.read(200)).decode("ascii", "replace").strip()
            raise SandboxFailed(f"parser_failed:{err or code}"[:80])
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
