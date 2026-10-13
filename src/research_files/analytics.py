"""Analytics and the end-of-parse audit, from the checkpointed state (pure; no I/O).

* ``Accumulator`` holds the per-file state the pipeline checkpoints with every unit: table
  profiles, duplicate-row hashes (first ``duplicate_rows_checked`` rows per table), sensitive data
  per column, document sections, term and entity counts (bounded), the metric ledger, exact finding
  counts per check and the searchable text already indexed.
* ``final_findings`` turns the accumulators into report rows with exact counts and example rows.
* ``build`` produces the analytics document: column profiles and correlations, document sections
  (with an extractive summary until a model summary replaces it), topics, entities, and charts.
  Every chart names its source: the table and rows, or the pages and sections, and whether it
  counts every row or a listed sample.

GUARANTEE(test_every_type_is_stored_parsed_audited_and_analysed)
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable
from typing import Any

from research_files.checks import Finding, MetricLedger, mask, sentences
from research_files.config import ResearchFilesConfig
from research_files.profile import Limits, TableProfile, summary

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its itself just me
    more most my no nor not now of off on once only or other our out over own same she should so
    some such than that the their them then there these they this those through to too under until
    up very was we were what when where which while who whom why will with would you your page
    section slide table figure shall may might must one two three new use used using within per
    via""".split()  # noqa: SIM905 - a word list reads better as text
)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{3,}")
_ENTITY = re.compile(r"\b(?:[A-Z][a-z]+(?:\s+(?:of\s+)?[A-Z][a-z]+){1,3}|[A-Z]{2,6}s?)\b")
TERM_CAP = 20000
EXAMPLES_PER_COLUMN = 3  # example cells listed per column and kind of personal data


def limits(cfg: ResearchFilesConfig) -> Limits:
    """Profile limits from config."""
    p, a = cfg.profile, cfg.audit
    return Limits(
        top_values=p.top_values,
        distinct_cap=p.distinct_cap,
        sample_size=p.sample_size,
        max_correlated_columns=p.max_correlated_columns,
        examples=a.max_findings_per_check,
        outlier_z=a.outlier_z,
        outlier_min=a.outlier_min_observations,
    )


class Accumulator:
    """The checkpointed state (see the module docstring)."""

    def __init__(self, data: dict[str, Any], cfg: ResearchFilesConfig) -> None:
        self.cfg = cfg
        self.lim = limits(cfg)
        self.tables = {k: TableProfile.from_json(v) for k, v in data.get("tables", {}).items()}
        self.dups: dict[str, dict[str, Any]] = data.get("dups", {})
        self._hashes = {
            k: set(_unpack(v.get("hashes", ""))) for k, v in self.dups.items() if not v["closed"]
        }
        self.sensitive: dict[str, dict[str, dict[str, int]]] = data.get("sensitive", {})
        self.sections: list[dict[str, Any]] = data.get("sections", [])
        self.terms: dict[str, int] = data.get("terms", {})
        self.entities: dict[str, list[Any]] = data.get("entities", {})  # name -> [count, locs]
        self.ledger = MetricLedger(seen=data.get("metrics", {}))
        self.counts: dict[str, int] = data.get("counts", {})
        self.indexed = int(data.get("indexed", 0))
        self.words = int(data.get("words", 0))
        self.stops: list[str] = data.get("stops", [])
        self.meta: dict[str, Any] = data.get("meta", {})

    def to_json(self) -> dict[str, Any]:
        """Serialisable state."""
        for key, hashes in self._hashes.items():
            self.dups[key]["hashes"] = _pack(hashes)
        return {
            "tables": {k: v.to_json() for k, v in self.tables.items()},
            "dups": self.dups,
            "sensitive": self.sensitive,
            "sections": self.sections,
            "terms": self.terms,
            "entities": self.entities,
            "metrics": self.ledger.seen,
            "counts": self.counts,
            "indexed": self.indexed,
            "words": self.words,
            "stops": self.stops,
            "meta": self.meta,
        }

    # ---------------------------------------------------------------- findings budget

    def keep(self, findings: Iterable[Finding]) -> list[Finding]:
        """Count every finding exactly; keep at most ``max_findings_per_check`` rows per check."""
        out = []
        cap = self.cfg.audit.max_findings_per_check
        for f in findings:
            n = self.counts.get(f.check, 0) + 1
            self.counts[f.check] = n
            if n <= cap:
                out.append(f)
        return out

    # ---------------------------------------------------------------- tables

    def add_rows(
        self,
        table: str,
        header: list[str] | None,
        header_row: int | None,
        rows: list[tuple[int, list[Any]]],
        location: dict[str, Any],
    ) -> list[Finding]:
        """Profile rows, check duplicates and sensitive data; findings for this unit."""
        from research_files.checks import SECRET_KINDS, sensitive_finding, sensitive_kinds

        profile = self.tables.get(table)
        if profile is None:
            first = (header_row or 0) + 1 if header_row else (rows[0][0] if rows else 1)
            profile = TableProfile.new(table, list(header or []), first)
            self.tables[table] = profile
            self.dups[table] = {"checked": 0, "dups": 0, "examples": [], "closed": False}
            self._hashes[table] = set()
        elif header and len(header) > len(profile.header):
            from research_files.profile import ColumnProfile

            for name in header[len(profile.header) :]:
                profile.header.append(name)
                profile.columns.append(ColumnProfile(name))
        dup = self.dups[table]
        hashes = self._hashes.get(table)
        found: list[Finding] = []
        scan = self.cfg.audit.pii_scan
        for row_no, cells in rows:
            profile.add(row_no, cells, self.lim)
            if hashes is not None and not dup["closed"]:
                h = hashlib.blake2b(repr(cells).encode(), digest_size=8).digest()
                if h in hashes:
                    dup["dups"] += 1
                    if len(dup["examples"]) < self.lim.examples:
                        dup["examples"].append(row_no)
                else:
                    hashes.add(h)
                dup["checked"] += 1
                if dup["checked"] >= self.cfg.audit.duplicate_rows_checked:
                    dup["closed"] = True
                    dup["hashes"] = ""
                    self._hashes.pop(table, None)
                    hashes = None
            if scan:
                joined = "\x1f".join(c for c in cells if isinstance(c, str))
                if joined and sensitive_kinds(joined):
                    for i, cell in enumerate(cells):
                        if not isinstance(cell, str):
                            continue
                        for kind in sensitive_kinds(cell):
                            column = profile.header[i] if i < len(profile.header) else f"col{i}"
                            per = self.sensitive.setdefault(table, {}).setdefault(column, {})
                            per[kind] = per.get(kind, 0) + 1
                            # a few example cells per column and kind; the column's summary
                            # finding carries the exact count (a column of emails is one problem,
                            # not one warning per cell)
                            if per[kind] <= EXAMPLES_PER_COLUMN or kind in SECRET_KINDS:
                                found.append(
                                    sensitive_finding(
                                        kind,
                                        {**_table_loc(location), "row": row_no, "column": column},
                                    )
                                )
        return self.keep(found)

    # ---------------------------------------------------------------- documents

    def add_text(
        self,
        seq: int,
        unit_kind: str,
        heading: str | None,
        text: str,
        location: dict[str, Any],
        stats: dict[str, Any],
    ) -> list[Finding]:
        """Sections, terms, entities and text checks for one document unit."""
        from research_files.checks import empty_sections, text_findings

        words = len(text.split())
        self.words += words
        if unit_kind in ("page", "slide", "section", "text"):
            first = next(sentences(text), "") if text.strip() else ""
            self.sections.append(
                {
                    "seq": seq,
                    "heading": (heading or location.get("section") or "")[:200],
                    "location": location,
                    "words": words,
                    "extract": mask(first)[:300],
                    "summary": None,
                    "summary_source": "extractive" if first else None,
                }
            )
        for word in _WORD.findall(text):
            w = word.lower()
            if w in STOPWORDS:
                continue
            self.terms[w] = self.terms.get(w, 0) + 1
        if len(self.terms) > TERM_CAP:
            self.terms = {k: v for k, v in self.terms.items() if v > 1}
        for match in _ENTITY.finditer(text):
            name = match.group().strip()
            if name.split()[0].lower() in STOPWORDS or len(name) < 3:
                continue
            entry = self.entities.setdefault(name, [0, []])
            entry[0] += 1
            if len(entry[1]) < 3:
                entry[1].append(location)
        if len(self.entities) > TERM_CAP:
            self.entities = {k: v for k, v in self.entities.items() if v[0] > 1}
        found = text_findings(text, location, pii=self.cfg.audit.pii_scan, ledger=self.ledger)
        if heading is not None and stats.get("empty"):
            found += empty_sections([(heading, "", location)])
        return self.keep(found)


def _table_loc(location: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in location.items() if k in ("member", "sheet", "table")}


def _pack(hashes: Iterable[bytes]) -> str:
    return base64.b64encode(b"".join(sorted(hashes))).decode()


def _unpack(blob: str) -> list[bytes]:
    raw = base64.b64decode(blob) if blob else b""
    return [raw[i : i + 8] for i in range(0, len(raw), 8)]


# ------------------------------------------------------------------ end-of-parse audit


def final_findings(acc: Accumulator) -> list[Finding]:
    """Summary findings with exact counts (see the module docstring)."""
    cfg = acc.cfg
    out: list[Finding] = []
    for key, prof in acc.tables.items():
        loc = {"table": key}
        rows_text = f"rows {prof.first_row}-{prof.first_row + prof.rows - 1}" if prof.rows else ""
        if prof.ragged_rows:
            out.append(
                Finding(
                    "warn",
                    "ragged_rows",
                    {**loc, "rows": prof.ragged_examples[:20]},
                    f"{prof.ragged_rows} row(s) have a different number of cells than the header.",
                    "Check for unquoted separators or missing cells.",
                )
            )
        dup = acc.dups.get(key, {})
        if dup.get("dups"):
            base = dup["checked"]
            out.append(
                Finding(
                    "warn",
                    "duplicate_rows",
                    {**loc, "rows": dup["examples"][:20]},
                    f"{dup['dups']} exact duplicate row(s) among the first {base} rows checked.",
                    "Remove the duplicates or confirm they are expected.",
                )
            )
        for col in prof.columns:
            cloc = {**loc, "column": col.name}
            if col.count and col.missing / col.count >= cfg.audit.missing_warn_fraction:
                out.append(
                    Finding(
                        "warn",
                        "missing_values",
                        {**cloc, "rows": rows_text},
                        f'Column "{col.name}" is missing {col.missing} of {col.count} values.',
                        "Fill the gaps or document why they are empty.",
                    )
                )
            if col.mismatches:
                out.append(
                    Finding(
                        "warn",
                        "type_mismatch",
                        {**cloc, "rows": col.mismatch_rows[:20]},
                        f'Column "{col.name}" is numeric but {col.mismatches} value(s)'
                        " are not numbers.",
                        "Correct the values or split the column.",
                    )
                )
            if col.outliers:
                out.append(
                    Finding(
                        "info",
                        "outliers",
                        {**cloc, "rows": col.outlier_rows[:20]},
                        f'Column "{col.name}" has {col.outliers} value(s) more than'
                        f" {cfg.audit.outlier_z:g} standard deviations from the mean.",
                        "Check whether these values are errors or real extremes.",
                    )
                )
            if len(col.date_formats) > 1:
                examples = {fmt: rows for fmt, rows in col.date_formats.items()}
                out.append(
                    Finding(
                        "warn",
                        "inconsistent_dates",
                        {**cloc, "examples": examples},
                        f'Column "{col.name}" mixes date formats: '
                        f"{', '.join(sorted(col.date_formats))}.",
                        "Use one date format (YYYY-MM-DD is unambiguous).",
                    )
                )
            if len(col.units) > 1:
                out.append(
                    Finding(
                        "warn",
                        "inconsistent_units",
                        {**cloc, "examples": col.units},
                        f'Column "{col.name}" mixes units: {", ".join(sorted(col.units))}.',
                        "Convert the values to one unit and name it in the header.",
                    )
                )
        for column, kinds in acc.sensitive.get(key, {}).items():
            total = sum(kinds.values())
            out.append(
                Finding(
                    "warn",
                    "sensitive_column",
                    {**loc, "column": column},
                    f'Column "{column}" holds personal data or secrets in {total}'
                    f" cell(s) ({', '.join(sorted(kinds))}); values are masked in the"
                    " analytics.",
                    "Remove or pseudonymise the column before sharing.",
                )
            )
    cap = cfg.audit.max_findings_per_check
    for check, n in sorted(acc.counts.items()):
        if n > cap:
            out.append(
                Finding(
                    "info",
                    "more_findings",
                    {"check": check},
                    f"{n} findings of type {check}; the first {cap} are listed.",
                    None,
                )
            )
    if acc.indexed >= cfg.parsing.max_indexed_text_bytes:
        mb = cfg.parsing.max_indexed_text_bytes // (1024 * 1024)
        out.append(
            Finding(
                "info",
                "search_index_capped",
                {},
                f"Questions search the first {mb} MB of text in full; later parts are"
                " searchable by excerpt only. Every row is still profiled and audited.",
                None,
            )
        )
    return out


# ------------------------------------------------------------------ analytics document


def _histogram(values: list[tuple[int, float]], bins: int) -> tuple[list[str], list[int]]:
    lo = min(v for _, v in values)
    hi = max(v for _, v in values)
    if hi == lo:
        return [f"{lo:g}"], [len(values)]
    width = (hi - lo) / bins
    counts = [0] * bins
    for _, v in values:
        counts[min(bins - 1, int((v - lo) / width))] += 1
    labels = [f"{lo + i * width:.4g} to {lo + (i + 1) * width:.4g}" for i in range(bins)]
    return labels, counts


def build(acc: Accumulator) -> dict[str, Any]:
    """The analytics document (see the module docstring)."""
    cfg = acc.cfg
    tables = []
    for key, prof in acc.tables.items():
        sensitive_cols = set(acc.sensitive.get(key, {}))
        cols = [summary(c, acc.lim, masked=c.name in sensitive_cols) for c in prof.columns]
        last = prof.first_row + prof.rows - 1
        every = {"table": key, "rows": [prof.first_row, last], "base": "every row"}
        charts: list[dict[str, Any]] = [
            {
                "id": f"{key}:missing",
                "title": "Missing values by column",
                "kind": "bar",
                "labels": [c.name for c in prof.columns],
                "values": [c.missing for c in prof.columns],
                "source": every,
            }
        ]
        for c in prof.columns:
            numeric = [(row, v) for _, row, v in c.sample if isinstance(v, int | float)]
            if c.mostly_numeric and len(numeric) >= 5 and len(charts) < 8:
                labels, counts = _histogram(numeric, cfg.profile.histogram_bins)
                charts.append(
                    {
                        "id": f"{key}:{c.name}:histogram",
                        "title": f"Distribution of {c.name}",
                        "kind": "histogram",
                        "labels": labels,
                        "values": counts,
                        "source": {
                            "table": key,
                            "column": c.name,
                            "rows": sorted(r for r, _ in numeric),
                            "base": f"sample of {len(numeric)} of {c.numeric} numeric rows",
                        },
                    }
                )
        for c, s in zip(prof.columns, cols, strict=True):
            if s["top_values"] and not c.mostly_numeric and len(charts) < 12:
                top = s["top_values"][:10]
                charts.append(
                    {
                        "id": f"{key}:{c.name}:top",
                        "title": f"Most common values of {c.name}",
                        "kind": "bar",
                        "labels": [t["value"] for t in top],
                        "values": [t["count"] for t in top],
                        "source": {
                            **every,
                            "column": c.name,
                            "example_rows": {t["value"]: t["example_rows"] for t in top},
                        },
                    }
                )
        tables.append(
            {
                "table": key,
                "rows": prof.rows,
                "first_row": prof.first_row,
                "columns": cols,
                "correlations": prof.correlations(),
                "charts": charts,
            }
        )
    document = None
    if acc.sections:
        topics = sorted(acc.terms.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
        entities = sorted(acc.entities.items(), key=lambda kv: (-kv[1][0], kv[0]))[:20]
        top_sections = sorted(acc.sections, key=lambda s: -s["words"])[:30]
        document = {
            "words": acc.words,
            "sections": acc.sections,
            "topics": [{"term": t, "count": n} for t, n in topics],
            "entities": [{"name": mask(n), "count": v[0], "locations": v[1]} for n, v in entities],
            "charts": [
                {
                    "id": "words_by_section",
                    "title": "Words per section",
                    "kind": "bar",
                    "labels": [s["heading"] or f"unit {s['seq']}" for s in top_sections],
                    "values": [s["words"] for s in top_sections],
                    "source": {
                        "locations": [s["location"] for s in top_sections],
                        "base": "every word of each section",
                    },
                },
                {
                    "id": "entities",
                    "title": "Most mentioned names",
                    "kind": "bar",
                    "labels": [mask(n) for n, _ in entities[:10]],
                    "values": [v[0] for _, v in entities[:10]],
                    "source": {
                        "locations": [v[1] for _, v in entities[:10]],
                        "base": "every mention; first three locations listed",
                    },
                },
            ],
        }
    return {"tables": tables, "document": document, "meta": acc.meta, "stops": acc.stops}
