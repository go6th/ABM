"""The run_python sandbox (documents/run_python.py and documents/run_child.py): model-written
code runs with the turn's tool results as ``data`` and its answer in ``result``, deterministically
and without network, processes or writes; every failure — bad code, an exception, an oversized
result, a runaway, a sandbox that cannot lock down — is one structured error; the code's sha256
is recorded, never the code. SYNTHETIC code only."""

from __future__ import annotations

import pytest

from documents import run_python as RP
from documents.tables import QueryError

pytestmark = pytest.mark.asyncio


async def test_run_python_computes_in_the_sandbox_without_network() -> None:
    """The code sees the turn's results as data and answers through result; the lockdown
    refuses the network, a process and a write; errors stay structured (see the module
    docstring)."""
    results = [
        {
            "table": "jobs",
            "row_count": 2,
            "rows": [
                {"row": 1, "cells": {"Salary": 10}},
                {"row": 2, "cells": {"Salary": 30}},
            ],
        }
    ]
    code = "result = sum(r['cells']['Salary'] for q in data for r in q['rows'])"
    out = await RP.run(code, results)
    assert out["result"] == 40
    assert out["code_sha256"] == RP.hash_code(code)
    again = await RP.run(code, results)  # the same code over the same data: the same answer
    assert (again["result"], again["code_sha256"]) == (out["result"], out["code_sha256"])
    said = await RP.run("print('hi there')", [])
    assert said["result"] is None and said["printed"].rstrip() == "hi there"
    for refused in (  # network, a process, a write: the class name is the error the model reads
        "import socket\nresult = bool(socket.create_connection(('192.0.2.1', 80), timeout=1))",
        "import subprocess\nresult = subprocess.run(['true'])",
        "result = open('/tmp/x-run-python-test', 'w')",
    ):
        with pytest.raises(QueryError, match="PermissionError"):
            await RP.run(refused, [])
    assert "PermissionError" not in RP.hash_code(code)  # the hash is opaque
    with pytest.raises(QueryError, match="at most"):
        await RP.run("   ", [])
    with pytest.raises(QueryError, match="at most"):
        await RP.run("x" * (RP.CODE_MAX_CHARS + 1), [])
    with pytest.raises(QueryError, match="ZeroDivisionError"):
        await RP.run("result = 1 / 0", [])
    with pytest.raises(QueryError, match="smaller answer"):
        await RP.run("result = 'x' * 17000", [])
    with pytest.raises(QueryError, match="past its minute"):
        await RP.run("while True: pass", [], timeout_s=3, cpu_s=55)


async def test_the_data_fit_drops_the_oldest_results() -> None:
    """fit_data keeps the newest results that fit the cap and says when it dropped any."""
    small, _ = RP.fit_data([{"a": 1}, {"b": 2}])
    assert small == [{"a": 1}, {"b": 2}]
    big = [{"blob": "x" * RP.DATA_MAX_CHARS}, {"new": 1}]
    kept, truncated = RP.fit_data(big)
    assert truncated and kept == [{"new": 1}]
