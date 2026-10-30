"""Helpers of the ``test_build_degrades_*`` tests: build a synthetic account and read every text
of every file the set holds (nested packages included), so a test can assert that a term reached
no file at all. Synthetic data only; no real names."""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Callable
from typing import Any

import agents.naming  # noqa: F401 - registers the client guard the builder applies (G9 rule)
from exports.builder import ExportBuilder
from exports.content import SnapshotContent
from exports.models import BuildResult
from tests.fakes.exports import MemoryStore

Make = Callable[..., ExportBuilder]

# a restricted account, spelled out here so no test names a real one
OTHER_NAME = "Zorblax Industries"
OTHER_DOMAIN = "zorblax.example"


def everything(data: bytes) -> str:
    """Every text of a package: the names and bodies of the members of zips and of the zips
    inside them (docx, xlsx), decoded; a body that is not a zip is decoded as it is."""
    out: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                out.append(name)
                out.append(everything(z.read(name)))
    except zipfile.BadZipFile:
        out.append(data.decode("utf-8", "ignore"))
    return "\n".join(out)


def stored_text(store: MemoryStore, result: BuildResult) -> str:
    """Every text of every file the result delivered (nested members included)."""
    return "\n".join(everything(store.objects[d.object_key]) for d in result.deliverables)


async def build(make: Make, content: SnapshotContent, **kw: Any) -> tuple[BuildResult, MemoryStore]:
    """Build the set from ``content`` over an in-memory store."""
    store = MemoryStore()
    result = await make(content, store, **kw).build_all(content.tenant_id, content.run_id)
    return result, store


def kinds_of(result: BuildResult) -> set[str]:
    """The file kinds the result stored."""
    return {d.kind for d in result.deliverables}


def log_lines(caplog: Any, event: str) -> list[logging.LogRecord]:
    """The log records of one event name (``exports.file_withheld``...)."""
    return [r for r in caplog.records if r.getMessage() == event]
