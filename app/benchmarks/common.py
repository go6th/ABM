"""Shared pieces for the Phase 0 benchmarks: provenance, table output."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Cell = str | int | float | None


@dataclass(frozen=True)
class Provenance:
    """Where results came from. Only live calls on real, reviewed golden accounts are evidence."""

    calls: Literal["fake", "recorded", "live"]
    data: Literal["synthetic", "real"]

    @property
    def is_evidence(self) -> bool:
        return self.calls == "live" and self.data == "real"

    def label(self) -> str:
        if self.is_evidence:
            return "live calls on real golden accounts"
        return (
            f"NOT EVIDENCE: {self.calls} calls on {self.data} golden accounts "
            "(harness check only; do not use for the decision)"
        )


def fmt(value: Cell) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def to_markdown(rows: list[dict[str, Cell]], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(fmt(r.get(c)) for c in columns) + " |" for r in rows]
    return "\n".join([header, sep, *body])


def write_tables(
    rows: list[dict[str, Cell]],
    columns: list[str],
    out_dir: Path,
    stem: str,
    *,
    provenance: Provenance,
    config_version: str,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({c: fmt(r.get(c)) for c in columns})
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(
        f"Provenance: {provenance.label()}\n\nConfig version: {config_version}\n\n"
        + to_markdown(rows, columns)
        + "\n",
        encoding="utf-8",
    )
    return csv_path, md_path
