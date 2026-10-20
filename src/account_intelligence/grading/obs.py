"""One span per grading batch (run id as trace id) and metrics by grade and outcome only.

No claim text, value or supporting sentence is ever logged or put on a span; there is no
tenant label on any metric.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from uuid import UUID

from opentelemetry import metrics, trace

from account_intelligence.connectors.obs import run_trace_context

_logger = logging.getLogger("account_intelligence.grading")
_tracer = trace.get_tracer("account_intelligence.grading")
_meter = metrics.get_meter("account_intelligence.grading")
_graded = _meter.create_counter(
    "grading_claims_graded", description="claims graded by product grade"
)
_dropped = _meter.create_counter(
    "grading_claims_dropped", description="claims dropped by reason code"
)


@contextmanager
def batch_span(run_id: UUID, size: int) -> Iterator[trace.Span]:
    """One span per grade_claims batch (no claim text)."""
    with _tracer.start_as_current_span(
        "grading.batch", context=run_trace_context(run_id), attributes={"batch_size": size}
    ) as span:
        yield span


def record_batch(grades: Mapping[str, int], drops: Mapping[str, int]) -> None:
    """Grade and drop counters (no tenant label)."""
    for grade, n in grades.items():
        _graded.add(n, {"product_grade": grade})
    for reason, n in drops.items():
        _dropped.add(n, {"reason": reason})
    # a structured event, not a JSON message: the sinks keep allow-listed fields only and drop
    # message text, so the drop reasons were invisible in Cloud Logging (audit 2026-10-20: a run
    # whose every claim failed value_not_in_sentence logged nothing about why)
    _logger.info(
        "grading.batch",
        extra={
            "count": sum(grades.values()),
            "dropped": sum(drops.values()),
            "error_code": ",".join(f"{k}={v}" for k, v in sorted(drops.items()))[:200],
            "graded": ",".join(f"{k}={v}" for k, v in sorted(grades.items()))[:200],
        },
    )
