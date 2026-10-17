"""The ``run_python`` tool of the Ask harness (2026-10-17): one piece of model-written Python
over this turn's tool results, run in the sandbox (``documents.run_child``, the parser sandbox's
lockdown), behind the per-tenant ``run_python`` feature flag.

The contract the model writes against (it is the tool description): the code runs with ``data``
— this turn's tool results so far, in order — and leaves its answer in ``result`` (JSON values
only); ``print`` output comes back as ``printed``. No network, no processes, no writes; a run
past ``RUN_TIMEOUT_S`` seconds is a ``timeout`` error, a result bigger than
``RESULT_MAX_CHARS`` a ``result_too_big`` one. ``PYTHONHASHSEED`` is pinned so the same code
over the same data gives the same answer.

Every call is logged with the sha256 of the code — the hash only, never the code (it can quote
the workspace's data). The caller (``replica.tools``) checks the flag before offering or
running the tool.

GUARANTEE(test_run_python_computes_in_the_sandbox_without_network)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from collections.abc import Sequence
from typing import Any

from documents.tables import QueryError

RUN_TIMEOUT_S = 60
RUN_CPU_S = 50
RUN_MEMORY_MB = 256
CODE_MAX_CHARS = 20_000
DATA_MAX_CHARS = 200_000
_LINE_LIMIT = 64 * 1024 * 1024

RUN_PYTHON_TOOL: dict[str, Any] = {
    "name": "run_python",
    "description": (
        "Run one piece of Python over this turn's tool results to compute an answer the other"
        " tools cannot (a rate, a median, a pivot, a join of two query_table results). The code"
        " runs with `data` — the tool results of this turn so far, in order — and must leave"
        " its answer in `result` (JSON values only; it comes back in the tool result). `print`"
        " output comes back as `printed`. There is no network, no files, no processes;"
        f" a run is at most {RUN_TIMEOUT_S} seconds and the result at most 16,000 characters."
    ),
    "input_schema": {
        "type": "object",
        "required": ["code"],
        "additionalProperties": False,
        "properties": {"code": {"type": "string", "maxLength": CODE_MAX_CHARS}},
    },
}


def hash_code(code: str) -> str:
    """The sha256 of the code — what the tool's log records, never the code itself."""
    return hashlib.sha256(code.encode()).hexdigest()


def fit_data(results: Sequence[Any]) -> tuple[list[Any], bool]:
    """The turn's tool results as the code's ``data``: the newest results that together fit
    ``DATA_MAX_CHARS`` (the oldest are dropped); (data, truncated)."""
    items = list(results)
    truncated = False
    # LOOP(run_python data fit): cap=DATA_MAX_CHARS; exit=len(items)<=1 or the data fits
    while len(items) > 1 and len(json.dumps(items, default=str)) > DATA_MAX_CHARS:
        items.pop(0)
        truncated = True
    return items, truncated


async def run(
    code: Any,
    results: Sequence[Any],
    *,
    timeout_s: float = RUN_TIMEOUT_S,
    cpu_s: int = RUN_CPU_S,
    memory_mb: int = RUN_MEMORY_MB,
) -> dict[str, Any]:
    """Run ``code`` over ``results`` in the sandbox and return its payload (the result, its
    ``code_sha256``, any ``printed`` text). Errors are structured ``QueryError``s the caller
    returns to the model."""
    if not isinstance(code, str) or not code.strip() or len(code) > CODE_MAX_CHARS:
        raise QueryError(
            "bad_code", f"code must be a string of at most {CODE_MAX_CHARS} characters"
        )
    data, truncated = fit_data(results)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "documents.run_child",
        str(memory_mb),
        str(cpu_s),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PYTHONHASHSEED": "0", "LANG": "C.UTF-8"},
        limit=_LINE_LIMIT,
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(json.dumps({"code": code, "data": data}).encode()), timeout_s
        )
    except TimeoutError:
        raise QueryError("timeout", "the code ran past its minute") from None
    except ValueError:
        raise QueryError("result_too_big", "the result is too large; compute a smaller one") \
            from None  # fmt: skip
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        raise QueryError("sandbox_failed", "the sandbox refused to run the code")
    try:
        record = json.loads(out)
    except json.JSONDecodeError:
        raise QueryError("bad_output", "the sandbox's answer could not be read") from None
    failed = record.get("error")
    if failed is not None:
        kind = str(failed.get("class") or "Error")
        if kind == "ResultTooBig":
            raise QueryError(
                "result_too_big",
                "the result is bigger than 16,000 characters; compute a smaller answer",
            )
        raise QueryError("python_failed", f"the code failed: {kind}")
    payload: dict[str, Any] = {"result": record.get("result"), "code_sha256": hash_code(code)}
    if record.get("printed"):
        payload["printed"] = record["printed"]
    if truncated:
        payload["data_truncated"] = True
    return payload
