"""Observability for the builders: one span per file and per verification (run id as the trace
id), metrics by file kind and outcome only (no tenant label), structured JSON logs that carry ids,
counts and error codes, never row text (canary test)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from opentelemetry import metrics, trace

from account_intelligence.connectors.obs import run_trace_context

_logger = logging.getLogger("exports")
_tracer = trace.get_tracer("exports")
_meter = metrics.get_meter("exports")
_files = _meter.create_counter("export_files", description="files built by kind and outcome")
_builds = _meter.create_counter("export_builds", description="builds by outcome")


@contextmanager
def span(name: str, run_id: str, **attributes: str | int) -> Iterator[trace.Span]:
    """A span under the run's trace (attributes are kinds and counts, never content)."""
    with _tracer.start_as_current_span(
        name, context=run_trace_context(UUID(run_id)), attributes=attributes
    ) as s:
        yield s


def file_outcome(kind: str, outcome: str) -> None:
    """Count one file by kind and outcome."""
    _files.add(1, {"kind": kind, "outcome": outcome})


def build_outcome(run_id: str, outcome: str, codes: tuple[str, ...], rows: dict[str, int]) -> None:
    """Count the build and log one line: run id, outcome, error codes and row counts only."""
    _builds.add(1, {"outcome": outcome})
    if codes:
        # the codes as a structured field: the JSON message below is reduced to a hash in the
        # cloud logs, so a failed build was otherwise invisible there (staging 2026-10-15)
        _logger.warning(
            "exports.build_failed",
            extra={"outcome": outcome, "error_code": ",".join(sorted(set(codes)))[:200]},
        )
    _logger.info(
        json.dumps(
            {
                "event": "exports.build",
                "run_id": run_id,
                "outcome": outcome,
                "error_codes": sorted(set(codes)),
                "rows": rows,
            },
            sort_keys=True,
        )
    )
