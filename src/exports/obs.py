"""Observability for the builders: one span per file and per verification (run id as the trace
id), metrics by file kind and outcome only (no tenant label), structured JSON logs that carry ids,
counts and error codes, never row text (canary test)."""

from __future__ import annotations

import json
import logging
from collections import Counter
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


def withheld_blocks(run_id: str, blocks: tuple[str, ...]) -> None:
    """Log the authored blocks a content rule kept out of a delivered set: how many, by rule.
    The set was delivered; this is the gap a reader of the Summary should know about."""
    by_rule = Counter(b.rsplit(":", 1)[-1] for b in blocks)
    _logger.info(
        "exports.withheld",
        extra={
            "count": len(blocks),
            "error_code": ",".join(f"{r}={n}" for r, n in sorted(by_rule.items()))[:200],
        },
    )


def leaks_withheld(count: int) -> None:
    """Log how many rendered texts the leak scan replaced by the withheld note in a delivered
    set. A count only: the patterns name restricted terms and people."""
    _logger.info("exports.leaks_withheld", extra={"count": count})


def file_outcome(kind: str, outcome: str) -> None:
    """Count one file by kind and outcome."""
    _files.add(1, {"kind": kind, "outcome": outcome})


def build_outcome(
    run_id: str, outcome: str, codes: tuple[str, ...], rows: dict[str, int], detail: str = ""
) -> None:
    """Count the build and log one line: run id, outcome, the rules that failed (with how many
    items each, in ``detail``) and row counts only; never a row's text."""
    _builds.add(1, {"outcome": outcome})
    if codes:
        # the rules as structured fields: the JSON message below is reduced to a hash in the
        # cloud logs, so a failed build was otherwise invisible there (staging 2026-10-15), and
        # the bare class ("banned_content") did not say which rule (staging 2026-10-21)
        _logger.warning(
            "exports.build_failed",
            extra={
                "outcome": outcome,
                "error_code": ",".join(sorted(set(codes)))[:200],
                "reason": detail[:200],
            },
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
