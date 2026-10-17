"""The run_python sandbox's child process: lock down, run one piece of model-written code,
print the result.

Run only by ``documents.run_python`` as ``python -I -m documents.run_child <memory MB> <CPU s>``
with an empty environment and ``{"code": ..., "data": ...}`` on stdin. Before the code runs it
installs the same lockdown as the parser sandbox (``documents.sandbox_child``, security audit
M2): address-space and CPU limits, no file creation, the kernel seccomp filter that refuses
network and new processes in native code, and the audit hook that refuses them in Python (on
Linux a filter that cannot be installed stops the run — fail closed). The code then runs with
``data`` (this turn's tool results so far) and ``result`` in scope; whatever it leaves in
``result`` is the answer. The child prints exactly one JSON line: ``{"result": ..., "printed":
...}`` or ``{"error": {"class": ...}}`` — the class name only, never the message, because a
message can quote the workspace's data.

GUARANTEE(test_run_python_computes_in_the_sandbox_without_network)
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

PRINT_MAX_CHARS = 4000
RESULT_MAX_CHARS = 16_000


def main(argv: list[str]) -> int:
    """Run the stdin payload under the lockdown; print one JSON result line (see the module
    docstring)."""
    from documents.sandbox_child import _audit, _limits, deny_network_and_processes

    memory_mb, cpu_s = int(argv[0]), int(argv[1])
    try:
        payload = json.loads(sys.stdin.read())
        code, data = str(payload["code"]), payload.get("data")
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError):
        sys.stdout.write(json.dumps({"error": {"class": "BadInput"}}) + "\n")
        return 0
    _limits(memory_mb, cpu_s)
    # raises SandboxUnavailable on Linux when the kernel filter cannot be installed: no run
    deny_network_and_processes()
    sys.addaudithook(_audit)
    namespace: dict[str, Any] = {"data": data, "result": None}
    printed = io.StringIO()
    real = sys.stdout
    sys.stdout = printed
    try:
        # the sandbox around this exec is the boundary: limits above, refusal in kernel and hook
        exec(compile(code, "<run_python>", "exec"), namespace)  # noqa: S102  # nosec B102 - model-written code runs only inside this locked-down child (see the module docstring)
    except Exception as error:  # noqa: BLE001 - the class name is the error the model reads
        real.write(json.dumps({"error": {"class": type(error).__name__}}) + "\n")
        return 0
    finally:
        sys.stdout = real
    text = json.dumps(namespace.get("result"), default=str)
    if len(text) > RESULT_MAX_CHARS:
        real.write(json.dumps({"error": {"class": "ResultTooBig"}}) + "\n")
        return 0
    out: dict[str, Any] = {"result": json.loads(text)}
    shown = printed.getvalue()
    if shown:
        out["printed"] = shown[:PRINT_MAX_CHARS]
    real.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
