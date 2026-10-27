"""The table specs are the files: every table ``parity.tables`` declares renders with the same
headers, rows, order and cell text in the files (workbooks through openpyxl and LibreOffice,
Word files through the Node renderer), and the generated TypeScript module is current."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed argv, no shell
import sys
from pathlib import Path
from typing import Any

import pytest

from parity import tables as T
from parity.bundle import AccountBundle
from parity.recalc import soffice_binary
from parity.render import DOCX_CLI, render_workbooks

ROOT = Path(__file__).resolve().parents[2]
office = pytest.mark.skipif(soffice_binary() is None, reason="LibreOffice is not installed")
node = pytest.mark.skipif(
    shutil.which("node") is None or not DOCX_CLI.exists(),
    reason="the Word renderer is not built",
)


def texts(rows: list[list[dict[str, Any]]]) -> list[list[str]]:
    return [[c["text"] for c in r] for r in rows]


def test_every_table_has_distinct_keys_and_an_order(synthetic_doc: dict[str, Any]) -> None:
    ids = [t.id for t in T.TABLES]
    assert len(ids) == len(set(ids))
    for spec in T.TABLES:
        keys = [c.key for c in spec.columns]
        assert len(keys) == len(set(keys)), spec.id
        rows = spec.rows(synthetic_doc)
        assert all(len(r) == len(spec.columns) for r in rows), spec.id
    summary = [t.id for t in T.tables_of(synthetic_doc, "summary")]
    assert summary[:2] == ["summary_legend", "summary_news"]
    assert summary[-1] == "summary_sources"
    evidence = [t.id for t in T.tables_of(synthetic_doc, "evidence")]
    assert evidence[0] == "evidence_sources"
    assert any(t.startswith(T.PART_C_PREFIX) for t in evidence)


def test_people_and_jobs_rows_follow_the_files_sort(synthetic_doc: dict[str, Any]) -> None:
    people = texts(T.table_rows(synthetic_doc, "people"))
    assert people[0][1] == "Pat Example" and people[0][0] == "1"
    levels = [T.LEVELS.index(r[3]) for r in people]
    assert levels == sorted(levels)
    jobs = texts(T.table_rows(synthetic_doc, "jobs"))
    first_seen = [r[5] for r in jobs]
    assert first_seen == sorted(first_seen, reverse=True)
    assert jobs[0][-1] == "Open posting"
    link = T.table_rows(synthetic_doc, "jobs")[0][-1]
    assert link["href"].startswith("https://")


def test_part_c_tables_exist_per_lane_with_rows(synthetic_doc: dict[str, Any]) -> None:
    lane = T.part_c_lanes(synthetic_doc)[0]
    spec = T.spec_of(T.PART_C_PREFIX + lane, synthetic_doc)
    assert spec is not None and spec.columns == T.PART_C_COLUMNS
    assert T.spec_of(T.PART_C_PREFIX + "Nowhere", synthetic_doc) is None
    assert T.spec_of("nope") is None


def test_the_generated_typescript_specs_are_current() -> None:
    script = ROOT / "scripts" / "parity" / "gen_table_specs.py"
    done = subprocess.run(  # nosec B603 - fixed argv
        [sys.executable, str(script), "--check"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


@office
@node
def test_specs_equal_the_rendered_files(
    synthetic_bundle: AccountBundle, synthetic_doc: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    render_workbooks(synthetic_bundle, out, recalc=True)
    import json

    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(synthetic_doc), encoding="utf-8")
    subprocess.run(  # nosec B603 - fixed argv
        [str(shutil.which("node")), str(DOCX_CLI), str(bundle_path), str(out)],
        check=True,
        capture_output=True,
    )
    for file in ("people", "jobs", "stack", "summary", "evidence"):
        dumped = T.dump_file_tables(out / T.file_name(synthetic_doc, file))
        specs = T.tables_of(synthetic_doc, file)
        if file == "stack":
            by_sheet = {d["sheet"]: d for d in dumped}
            pairs = [(s, by_sheet[s.sheet]) for s in specs]
        elif file in ("people", "jobs"):
            pairs = [(specs[0], dumped[0])]
        else:
            assert len(dumped) == len(specs), (file, [d["headers"] for d in dumped])
            pairs = list(zip(specs, dumped, strict=True))
        for spec, d in pairs:
            assert spec.header_row(synthetic_doc) == d["headers"], spec.id
            assert texts(spec.rows(synthetic_doc)) == d["rows"], spec.id
