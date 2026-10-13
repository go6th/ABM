"""Streaming, resumable column profiles (pure; no I/O).

A ``TableProfile`` is updated one row at a time and serialised to JSON after every stored unit, so
a resumed parse continues with exactly the state an uninterrupted parse would have. Every
accumulator is bounded:

* numeric moments by Welford (count, mean, M2), min and max;
* missing values (exact), distinct values (exact up to ``distinct_cap``, then "at least");
* top values (exact counts while the distinct set is under the cap) with up to 3 example rows;
* a deterministic bottom-k sample of row numbers per column (hash of the row number), used for
  quantiles and histograms, which therefore name the exact rows they came from;
* type mismatches (a non-numeric cell in a mostly numeric column) and outliers (|z| above the
  configured bound once enough values were seen), counted exactly with example rows;
* date-format and unit variety per column; pairwise Pearson sums over the first numeric columns.

GUARANTEE(test_kill_mid_parse_then_resume_repeats_nothing_and_loses_nothing)
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

NUMBER = re.compile(r"^\s*[-+]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?([eE][-+]?\d+)?\s*$")
UNIT = re.compile(r"^\s*[-+]?\d+(?:[.,]\d+)?\s*([A-Za-z%$€£]{1,6})\s*$")
CURRENCY_PREFIX = re.compile(r"^\s*([$€£])\s*[-+]?\d")
DATE_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("YYYY-MM-DD", re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")),
    ("DD/MM/YYYY or MM/DD/YYYY", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("DD-MM-YYYY", re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")),
    ("DD.MM.YYYY", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")),
    ("YYYY/MM/DD", re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")),
    ("Month D, YYYY", re.compile(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}$")),
    ("D Month YYYY", re.compile(r"^\d{1,2} [A-Za-z]{3,9} \d{4}$")),
)
MISSING = frozenset({"", "na", "n/a", "null", "none", "nan", "-", "--", "?"})


def to_number(cell: Any) -> float | None:
    """A float from a numeric cell or a plain numeric string (thousands separators allowed)."""
    if isinstance(cell, bool):
        return None
    if isinstance(cell, int | float):
        value = float(cell)
        return value if math.isfinite(value) else None
    if not isinstance(cell, str) or not cell or len(cell) > 40 or not NUMBER.match(cell):
        return None
    try:
        value = float(cell.replace(",", "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def is_missing(cell: Any) -> bool:
    """Empty, None or a missing-value marker."""
    return cell is None or (isinstance(cell, str) and cell.strip().lower() in MISSING)


def date_format(cell: str) -> str | None:
    """The date format class of a string cell, or None."""
    if not cell or not cell[0].isalnum() or len(cell) > 32:
        return None
    for name, pattern in DATE_FORMATS:
        if pattern.match(cell):
            return name
    return None


def unit_of(cell: str) -> str | None:
    """The unit suffix (or currency prefix) of a quantity cell, or None."""
    match = UNIT.match(cell)
    if match:
        return match.group(1).lower()
    prefix = CURRENCY_PREFIX.match(cell)
    return prefix.group(1) if prefix else None


def _rank(row: int) -> int:
    return int.from_bytes(hashlib.blake2b(row.to_bytes(8, "big"), digest_size=8).digest(), "big")


@dataclass
class ColumnProfile:
    """One column's accumulators (see the module docstring)."""

    name: str
    count: int = 0
    missing: int = 0
    numeric: int = 0
    mean: float = 0.0
    m2: float = 0.0
    min: float | None = None
    max: float | None = None
    text_min_len: int | None = None
    text_max_len: int | None = None
    distinct: dict[str, int] = field(default_factory=dict)
    distinct_overflow: bool = False
    examples: dict[str, list[int]] = field(default_factory=dict)
    sample: list[list[Any]] = field(default_factory=list)  # [rank, row, value]
    mismatches: int = 0
    mismatch_rows: list[int] = field(default_factory=list)
    outliers: int = 0
    outlier_rows: list[int] = field(default_factory=list)
    date_formats: dict[str, list[int]] = field(default_factory=dict)
    units: dict[str, list[int]] = field(default_factory=dict)

    @property
    def stdev(self) -> float | None:
        """Sample standard deviation of the numeric values."""
        return math.sqrt(self.m2 / (self.numeric - 1)) if self.numeric > 1 else None

    @property
    def mostly_numeric(self) -> bool:
        """At least 80% of the present values are numbers."""
        present = self.count - self.missing
        return present > 0 and self.numeric >= 0.8 * present


@dataclass
class Limits:
    """Accumulator bounds from config."""

    top_values: int = 20
    distinct_cap: int = 10000
    sample_size: int = 2000
    max_correlated_columns: int = 12
    examples: int = 50
    outlier_z: float = 6.0
    outlier_min: int = 1000


@dataclass
class TableProfile:
    """A table's profile: columns, rows seen and correlation sums."""

    table: str
    header: list[str]
    rows: int = 0
    first_row: int = 1  # the source row number of the first data row
    ragged_rows: int = 0
    ragged_examples: list[int] = field(default_factory=list)
    columns: list[ColumnProfile] = field(default_factory=list)
    corr: dict[str, list[float]] = field(
        default_factory=dict
    )  # "i,j" -> [n, sx, sy, sxx, syy, sxy]

    @classmethod
    def new(cls, table: str, header: list[str], first_row: int) -> TableProfile:
        """A fresh profile for a header."""
        return cls(table, header, first_row=first_row, columns=[ColumnProfile(h) for h in header])

    def to_json(self) -> dict[str, Any]:
        """Serialisable state."""
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TableProfile:
        """Inverse of ``to_json``."""
        cols = [ColumnProfile(**c) for c in data.get("columns", [])]
        return cls(**{**data, "columns": cols})

    def add(self, row_no: int, cells: list[Any], lim: Limits) -> None:
        """Add one data row (``row_no`` is the 1-based source row number)."""
        self.rows += 1
        width = len(self.columns)
        if len(cells) != width:
            self.ragged_rows += 1
            if len(self.ragged_examples) < lim.examples:
                self.ragged_examples.append(row_no)
            if len(cells) > width:
                for i in range(width, len(cells)):
                    name = f"column_{i + 1}"
                    self.header.append(name)
                    self.columns.append(ColumnProfile(name))
                width = len(self.columns)
        numbers: list[float | None] = []
        rank = _rank(row_no)
        for i in range(width):
            cell = cells[i] if i < len(cells) else None
            numbers.append(self._cell(self.columns[i], row_no, rank, cell, lim))
        self._correlate(numbers, lim)

    def _cell(
        self, col: ColumnProfile, row_no: int, rank: int, cell: Any, lim: Limits
    ) -> float | None:
        col.count += 1
        if is_missing(cell):
            col.missing += 1
            return None
        value = to_number(cell)
        text = cell if isinstance(cell, str) else str(cell)
        if value is not None:
            col.numeric += 1
            if col.numeric > lim.outlier_min and col.stdev:
                z = abs(value - col.mean) / col.stdev
                if z > lim.outlier_z:
                    col.outliers += 1
                    if len(col.outlier_rows) < lim.examples:
                        col.outlier_rows.append(row_no)
            delta = value - col.mean
            col.mean += delta / col.numeric
            col.m2 += delta * (value - col.mean)
            col.min = value if col.min is None else min(col.min, value)
            col.max = value if col.max is None else max(col.max, value)
        else:
            if col.numeric > 20 and col.mostly_numeric:
                col.mismatches += 1
                if len(col.mismatch_rows) < lim.examples:
                    col.mismatch_rows.append(row_no)
            length = len(text)
            col.text_min_len = length if col.text_min_len is None else min(col.text_min_len, length)
            col.text_max_len = length if col.text_max_len is None else max(col.text_max_len, length)
            if isinstance(cell, str):
                fmt = date_format(cell)
                if fmt is not None:
                    rows = col.date_formats.setdefault(fmt, [])
                    if len(rows) < 3:
                        rows.append(row_no)
                unit = unit_of(cell)
                if unit is not None and (unit in col.units or len(col.units) < 20):
                    rows = col.units.setdefault(unit, [])
                    if len(rows) < 3:
                        rows.append(row_no)
        key = text[:120]
        if not col.distinct_overflow:
            if key in col.distinct:
                col.distinct[key] += 1
            elif len(col.distinct) < lim.distinct_cap:
                col.distinct[key] = 1
            else:
                # past the cap the exact table is no use (top values are no longer exact): drop
                # it so the checkpoint stays small; the summary says "at least distinct_cap"
                col.distinct_overflow = True
                col.distinct, col.examples = {}, {}
        if not col.distinct_overflow:
            seen = col.examples.setdefault(key, [])
            if len(seen) < 3:
                seen.append(row_no)
        if len(col.sample) < lim.sample_size:
            col.sample.append([rank, row_no, value if value is not None else key[:40]])
            if len(col.sample) == lim.sample_size:
                col.sample.sort(key=lambda s: s[0])
        elif rank < col.sample[-1][0]:
            col.sample[-1] = [rank, row_no, value if value is not None else key[:40]]
            col.sample.sort(key=lambda s: s[0])
        return value

    def _correlate(self, numbers: list[float | None], lim: Limits) -> None:
        numeric_cols = [i for i, c in enumerate(self.columns) if c.mostly_numeric][
            : lim.max_correlated_columns
        ]
        for a_pos, i in enumerate(numeric_cols):
            x = numbers[i] if i < len(numbers) else None
            if x is None:
                continue
            for j in numeric_cols[a_pos + 1 :]:
                y = numbers[j] if j < len(numbers) else None
                if y is None:
                    continue
                acc = self.corr.setdefault(f"{i},{j}", [0.0] * 6)
                acc[0] += 1
                acc[1] += x
                acc[2] += y
                acc[3] += x * x
                acc[4] += y * y
                acc[5] += x * y

    def correlations(self) -> list[dict[str, Any]]:
        """Pearson r for every pair with at least 3 joint values."""
        out = []
        for key, (n, sx, sy, sxx, syy, sxy) in sorted(self.corr.items()):
            if n < 3:
                continue
            vx, vy = n * sxx - sx * sx, n * syy - sy * sy
            if vx <= 0 or vy <= 0:
                continue
            i, j = (int(p) for p in key.split(","))
            r = (n * sxy - sx * sy) / math.sqrt(vx * vy)
            out.append(
                {"a": self.columns[i].name, "b": self.columns[j].name, "r": round(r, 4), "n": n}
            )
        return out


def summary(col: ColumnProfile, lim: Limits, *, masked: bool = False) -> dict[str, Any]:
    """The public profile of one column (quantiles from the sample; top values exact)."""
    present = col.count - col.missing
    numeric_sample = sorted(v for _, _, v in col.sample if isinstance(v, int | float))
    quantiles = None
    if col.mostly_numeric and numeric_sample:
        q = numeric_sample
        quantiles = {
            "p25": q[int(0.25 * (len(q) - 1))],
            "p50": q[int(0.5 * (len(q) - 1))],
            "p75": q[int(0.75 * (len(q) - 1))],
        }
    top = sorted(col.distinct.items(), key=lambda kv: (-kv[1], kv[0]))[: lim.top_values]
    return {
        "name": col.name,
        "type": "number" if col.mostly_numeric else ("empty" if present == 0 else "text"),
        "count": col.count,
        "missing": col.missing,
        "distinct": lim.distinct_cap if col.distinct_overflow else len(col.distinct),
        "distinct_exact": not col.distinct_overflow,
        "mean": round(col.mean, 6) if col.numeric else None,
        "stdev": round(col.stdev, 6) if col.stdev is not None else None,
        "min": col.min,
        "max": col.max,
        "quantiles": quantiles,
        "quantile_base": f"sample of {len(numeric_sample)} rows" if quantiles else None,
        "top_values": []
        if masked or col.distinct_overflow
        else [
            {"value": value, "count": n, "example_rows": col.examples.get(value, [])}
            for value, n in top
        ],
        "masked": masked,
    }
