"""The sweeper's documented jobs are the jobs it runs (ADR-0132; crude, but it stops drift).

``runs.sweeper``'s module docstring lists the jobs of the worker's sweeper pass as a numbered
list, each naming the function it calls; ``api.main.sweep_pass`` runs each job in its own
``try``. Until 2026-11-03 the docs named three to five jobs while the pass ran six.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap

from account_intelligence.api import main
from account_intelligence.runs import sweeper


def _documented() -> list[str]:
    doc = sweeper.__doc__ or ""
    return re.findall(r"^\d+\. ``(\w+)``", doc, flags=re.M)


def _run() -> list[str]:
    """The function each ``try`` block of ``sweep_pass`` awaits first, in order."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(main.sweep_pass)))
    body = tree.body[0].body  # type: ignore[attr-defined]
    called: list[str] = []
    for node in body:
        if not isinstance(node, ast.Try):
            continue
        awaited = next(n for n in ast.walk(node) if isinstance(n, ast.Await))
        call = awaited.value
        assert isinstance(call, ast.Call)
        func = call.func
        called.append(func.id if isinstance(func, ast.Name) else ast.unparse(func))
    return called


def test_the_number_of_sweeper_jobs_equals_the_number_documented() -> None:
    documented, run = _documented(), _run()
    assert len(documented) == len(run), (documented, run)
    assert documented == run  # the same jobs, in the same order
