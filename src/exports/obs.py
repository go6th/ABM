"""Observability for the builders: one span per file and per verification (run id as the trace
id), metrics by file kind and outcome only (no tenant label), structured JSON logs that carry ids,
counts and error codes, never row text (canary test)."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Iterator, Mapping
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


_ID_LIKE = re.compile(r"^[0-9a-f-]{8,}$")
# the longest a structured log field may be (the cloud logs cut longer ones)
LOG_FIELD = 200


def where_part(where: str) -> str:
    """The part of a file a ``where`` label names, without the row ids in it
    (``summary.sections.narrative.5b0c...`` is ``summary.sections.narrative``)."""
    return ".".join(p for p in where.split(".") if not _ID_LIKE.match(p))


def count_lines(counts: Mapping[tuple[str, str], int]) -> tuple[str, ...]:
    """``part:rule=count`` for each (part, rule), sorted: neutral names and numbers only."""
    return tuple(f"{part}:{rule}={n}" for (part, rule), n in sorted(counts.items()))


def _chunks(lines: tuple[str, ...]) -> Iterator[str]:
    """The lines joined by commas in pieces that fit one log field, so a long list is logged in
    several lines instead of cut."""
    piece = ""
    for line in lines:
        if piece and len(piece) + len(line) + 1 > LOG_FIELD:
            yield piece
            piece = ""
        piece = f"{piece},{line}" if piece else line
    if piece:
        yield piece


def withheld_blocks(run_id: str, blocks: tuple[str, ...]) -> None:
    """Log the authored blocks a content rule kept out of a delivered set: how many, by part of a
    file and rule (``jobs:missing_url=3``). The set was delivered; this is the gap a reader of the
    files should know about, and the log says which file part and which rule without the data."""
    by_part = Counter((where_part(b.split(":", 1)[0]), b.rsplit(":", 1)[-1]) for b in blocks)
    for piece in _chunks(count_lines(by_part)):
        _logger.info("exports.withheld", extra={"count": len(blocks), "error_code": piece})


def leaks_withheld(count: int, parts: tuple[str, ...] = ()) -> None:
    """Log how many rendered texts a client-output rule replaced by the withheld note in a
    delivered set, by part of a file and rule when known. Counts and rule names only: the
    patterns name restricted terms and people."""
    for piece in list(_chunks(parts)) or [""]:
        _logger.info("exports.leaks_withheld", extra={"count": count, "error_code": piece})


def file_withheld(run_id: str, kind: str, rule: str) -> None:
    """Log (a warning) a file the set does not hold, with the rule that kept it out: the research
    is delivered without it and ends PARTIAL ``files_withheld``."""
    _logger.warning(
        "exports.file_withheld", extra={"error_code": f"{kind}:{rule}"[:LOG_FIELD], "count": 1}
    )


def late_replaced(run_id: str, items: tuple[str, ...]) -> None:
    """Log (a warning) the strings the last line of defence replaced after the final scan fired:
    a text path the mapping's checks missed, by file kind and rule. Counts only."""
    for piece in _chunks(items):
        _logger.warning("exports.late_replaced", extra={"error_code": piece, "count": len(items)})


def members_scrubbed(run_id: str, items: tuple[str, ...]) -> None:
    """Log the workspace members whose text was scrubbed (``member=count``)."""
    for piece in _chunks(items):
        _logger.info("exports.members_scrubbed", extra={"error_code": piece, "count": len(items)})


def check_not_run(run_id: str, name: str, outcome: str) -> None:
    """Log (a warning) a verification check that did not run in a delivered set, with its reason:
    the set was built without that check, which the manifest also lists."""
    _logger.warning(
        "exports.check_not_run",
        extra={"reason": outcome[:200], "error_code": name},
    )


def build_crashed(error: BaseException) -> None:
    """Log (with the traceback, from inside the ``except`` block) a build that raised something
    that is not a typed export error: the class only, never the message, which can hold text."""
    _logger.exception("exports.build_crashed", extra={"error_class": type(error).__name__})


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
