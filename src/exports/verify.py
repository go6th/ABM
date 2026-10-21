"""Self-checks used by the builder and the harness (spec 8.2). The full quality gate stays behind
``GatePort``; these are the checks the builder must pass before it may store a set:

* ``scan_package``: every member of a docx, xlsx or zip (document XML, relationships, cells,
  properties, member names) through the client-output rules plus the run's forbidden terms;
* ``docx_row_keys`` / ``xlsx_row_keys``: the visible row keys of every table in file order
  (row conservation re-read from the produced bytes, B3);
* ``pdf_row_presence``: Word to PDF, every row key found exactly once and in order (the
  lost-row-at-page-break defect); ``not_run`` with a reason when no converter is installed;
* ``recalc_check``: workbook recalculated, zero error values, formula totals equal the counts
  computed in code (B12); ``not_run`` with a reason when no engine is installed.
* ``docx_rowcheck``: every table row in the Word file appears in the rendered PDF; fails closed
  when LibreOffice or pdftotext is unavailable.
* ``doc_checks``: no orphan citations, banned dashes, stale phrases, or excluded names in the
  rendered document; runs on the real built file's extracted text.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from lxml import etree
from openpyxl import load_workbook

from account_intelligence.config import CONFIG_DIR
from exports.policy import leak_hits
from exports.ports import CheckResult, PdfRenderPort

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT = (".xml", ".rels", ".json", ".md", ".txt")
_ERRORS = ("#REF!", "#VALUE!", "#NAME?", "#DIV/0!", "#N/A", "#NUM!", "#NULL!")
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
_REF = re.compile(r"^[A-Z]{1,3}-\d{4,}$")


def _members(
    data: bytes, prefix: str = "", scanned: frozenset[str] = frozenset()
) -> Iterable[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            body = z.read(name)
            yield f"{prefix}{name} (name)", name.encode()
            if name.endswith((".docx", ".xlsx")):
                if hashlib.sha256(body).hexdigest() in scanned:
                    continue  # the identical file was already scanned on its own
                yield from _members(body, f"{prefix}{name}!")
            elif name.endswith(_TEXT):
                yield f"{prefix}{name}", body


def scan_package(
    data: bytes, extra_terms: Sequence[str], scanned: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """Every hit in every member, as ``member: pattern`` (patterns only, never text). Hashes are
    expected in ``MANIFEST.json`` and the render data, so the internal-hash rule skips them.
    ``scanned`` holds SHA-256 digests of nested files already scanned byte for byte."""
    hits: list[str] = []
    for where, body in _members(data, scanned=scanned):
        text = body.decode("utf-8", "ignore")
        for pattern in leak_hits(text, extra_terms):
            if pattern == "internal_hash" and where.split(" ")[0].endswith(
                ("MANIFEST.json", ".json")
            ):
                continue
            hits.append(f"{where}: {pattern}")
    return tuple(hits)


def docx_text(data: bytes) -> str:
    """All text of the document body, header and footer (for content checks)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        out = []
        for name in sorted(z.namelist()):
            if name.startswith("word/") and name.endswith(".xml"):
                root = etree.fromstring(z.read(name), parser=_PARSER)
                out.append("".join(t.text or "" for t in root.iter(f"{W}t")))
        return "\n".join(out)


def docx_row_keys(data: bytes) -> tuple[str, ...]:
    """First-cell text of every table row that looks like a row key, in document order."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read("word/document.xml"), parser=_PARSER)
    keys = []
    for tr in root.iter(f"{W}tr"):
        tc = tr.find(f"{W}tc")
        if tc is None:
            continue
        text = "".join(t.text or "" for t in tc.iter(f"{W}t"))
        if _REF.match(text):
            keys.append(text)
    return tuple(keys)


def xlsx_row_keys(data: bytes, sheet: str) -> tuple[str, ...]:
    """Column A (Ref) of the data rows of ``sheet``, in row order."""
    wb = load_workbook(io.BytesIO(data), read_only=True)
    try:
        ws = wb[sheet]
        return tuple(
            str(row[0])
            for row in ws.iter_rows(min_row=2, max_col=1, values_only=True)
            if row and isinstance(row[0], str) and _REF.match(row[0])
        )
    finally:
        wb.close()


def keys_once_in_order(text: str, keys: Sequence[str]) -> tuple[str, ...]:
    """Problems: a key missing, found more than once, or out of order in ``text``."""
    problems: list[str] = []
    last = -1
    for key in keys:
        found = [m.start() for m in re.finditer(rf"(?<![A-Z0-9-]){re.escape(key)}(?![0-9])", text)]
        if len(found) != 1:
            problems.append(f"{key}: found {len(found)} times")
            continue
        if found[0] < last:
            problems.append(f"{key}: out of order")
        last = found[0]
    return tuple(problems)


class LibreOffice:
    """``PdfRenderPort`` and ``RecalcPort`` over a headless LibreOffice (``soffice``), with a
    timeout, a private profile, a cleared environment and at most one re-run."""

    def __init__(self, timeout_s: float = 120.0, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("soffice")
        self.timeout_s = timeout_s

    def _convert(self, body: bytes, suffix: str, target: str) -> bytes | None:
        if self.binary is None:
            return None
        # LOOP(converter re-run): cap=2 attempts; exit=converted, None (not_run with reason)
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / f"in{suffix}"
                src.write_bytes(body)
                out_dir = Path(tmp) / "out"
                out_dir.mkdir()
                cmd = [
                    self.binary,
                    f"-env:UserInstallation=file://{tmp}/profile",
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    target,
                    "--outdir",
                    str(out_dir),
                    str(src),
                ]
                try:
                    subprocess.run(  # nosec B603 - fixed argv, no shell
                        cmd,
                        check=True,
                        capture_output=True,
                        timeout=self.timeout_s,
                        env={
                            "HOME": tmp,
                            "TMPDIR": tmp,
                            "LC_ALL": "C.UTF-8",
                            "PATH": "/usr/bin:/bin",
                        },
                    )
                except (subprocess.SubprocessError, OSError):
                    continue
                produced = sorted(out_dir.glob("in.*"))
                if produced:
                    return produced[0].read_bytes()
        return None

    def pdf_bytes(self, docx: bytes) -> bytes | None:
        """The PDF rendering of a Word document, or None without a converter."""
        return self._convert(docx, ".docx", "pdf")

    def text_of_pdf(self, pdf: bytes) -> str | None:
        """A PDF's text (``pdftotext -layout``), or None without the tool."""
        pdftotext = shutil.which("pdftotext")
        if pdftotext is None:
            return None
        try:
            done = subprocess.run(  # nosec B603 - fixed argv, no shell
                [pdftotext, "-layout", "-", "-"],
                input=pdf,
                capture_output=True,
                check=True,
                timeout=self.timeout_s,
                env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
            )
        except (subprocess.SubprocessError, OSError):
            return None
        return done.stdout.decode("utf-8", "ignore")

    def pdf_text(self, docx: bytes) -> str | None:
        """PDF rendering of the document, as text (``pdftotext -layout``), or None."""
        pdf = self.pdf_bytes(docx)
        return None if pdf is None else self.text_of_pdf(pdf)

    def recalculated(self, xlsx: bytes) -> bytes | None:
        """The workbook re-saved by LibreOffice (formulas computed and cached), or None."""
        return self._convert(xlsx, ".xlsx", "xlsx:Calc MS Excel 2007 XML")


def pdf_row_presence(docx: bytes, keys: Sequence[str], pdf: object) -> CheckResult:
    """Every row key once and in order in the PDF text (``not_run`` without a converter)."""
    text = pdf.pdf_text(docx)  # type: ignore[attr-defined]
    if text is None:
        return CheckResult("pdf_row_presence", "not_run", "no Word to PDF converter installed")
    problems = keys_once_in_order(text, keys)
    return CheckResult("pdf_row_presence", "failed" if problems else "passed", details=problems)


def recalc_check(
    xlsx: bytes, expected: Mapping[tuple[str, str], int], recalc: object
) -> CheckResult:
    """Zero error values and each formula cell (sheet, cell) equal to its code-computed count."""
    done = recalc.recalculated(xlsx)  # type: ignore[attr-defined]
    if done is None:
        return CheckResult("workbook_recalc", "not_run", "no spreadsheet engine installed")
    wb = load_workbook(io.BytesIO(done), data_only=True)
    problems: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value in _ERRORS:
                    problems.append(f"{ws.title}!{cell.coordinate}: {cell.value}")
    for (sheet, coord), value in expected.items():
        got = wb[sheet][coord].value
        if got != value:
            problems.append(f"{sheet}!{coord}: {got!r} != {value}")
    return CheckResult(
        "workbook_recalc", "failed" if problems else "passed", details=tuple(problems)
    )


def _skill_scripts_dir() -> Path:
    """Directory containing the skill's gate scripts (rowcheck.py, doc_checks.py)."""
    return CONFIG_DIR / "skill" / "archive" / "scripts"


def _run_rowcheck(docx: bytes, pdf: PdfRenderPort) -> CheckResult:
    """Run the skill's rowcheck.py on the docx and its PDF rendering.

    Fails closed: if LibreOffice or pdftotext is unavailable, the check fails.
    """
    pdf_bytes = pdf.pdf_bytes(docx)
    if pdf_bytes is None:
        return CheckResult(
            "docx_rowcheck",
            "failed",
            "Word to PDF converter unavailable (LibreOffice not installed or failed)",
        )
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return CheckResult("docx_rowcheck", "failed", "pdftotext not installed")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        docx_path = tmp_path / "check.docx"
        pdf_path = tmp_path / "check.pdf"
        docx_path.write_bytes(docx)
        pdf_path.write_bytes(pdf_bytes)
        script = _skill_scripts_dir() / "rowcheck.py"
        try:
            result = subprocess.run(  # nosec B603 - fixed argv, no shell
                [sys.executable, str(script), "check"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmp,
            )
        except (subprocess.SubprocessError, OSError) as e:
            return CheckResult("docx_rowcheck", "failed", f"rowcheck execution failed: {e}")
    if result.returncode != 0:
        return CheckResult(
            "docx_rowcheck", "failed", result.stdout.strip() or result.stderr.strip()
        )
    return CheckResult("docx_rowcheck", "passed")


def _extract_doc_text(data: bytes) -> str:
    """Extract visible text from a docx, pptx, or pdf file for doc_checks."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            parts = []
            for name in sorted(names):
                if name.endswith((".docx", ".pptx")):
                    parts.append(_extract_doc_text(z.read(name)))
                elif name.startswith(
                    ("word/", "ppt/slides/", "ppt/notesSlides/")
                ) and name.endswith(".xml"):
                    root = etree.fromstring(z.read(name), parser=_PARSER)
                    parts.append("".join(t.text or "" for t in root.iter(f"{W}t")))
                elif name in ("docProps/core.xml", "docProps/app.xml"):
                    root = etree.fromstring(z.read(name), parser=_PARSER)
                    parts.append("".join(t.text or "" for t in root.iter()))
                elif name.endswith((".md", ".txt")):
                    parts.append(z.read(name).decode("utf-8", "ignore"))
            return "\n".join(parts)
    except zipfile.BadZipFile:
        return data.decode("utf-8", "ignore")


def _run_doc_checks(
    data: bytes,
    kind: str,
    account_ids: Sequence[str],
    stale_phrases: Sequence[str],
    excluded_names: Sequence[str],
    verbatim_quotes: Sequence[str],
) -> CheckResult:
    """Run the skill's doc_checks.py on the extracted document text.

    Uses the real built document text and the account's valid source IDs.
    """
    text = _extract_doc_text(data)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        text_path = tmp_path / "doc.txt"
        ids_path = tmp_path / "ids.json"
        text_path.write_text(text, encoding="utf-8")
        import json

        ids_path.write_text(json.dumps(list(account_ids)), encoding="utf-8")
        quotes_path = tmp_path / "quotes.json"
        quotes_path.write_text(json.dumps(list(verbatim_quotes)), encoding="utf-8")
        script = _skill_scripts_dir() / "doc_checks.py"
        cmd = [
            sys.executable,
            str(script),
            str(text_path),
            "--ids",
            str(ids_path),
            "--verbatim-quotes",
            str(quotes_path),
        ]
        if stale_phrases:
            cmd.extend(["--stale", *stale_phrases])
        if excluded_names:
            cmd.extend(["--exclude", *excluded_names])
        try:
            result = subprocess.run(  # nosec B603 - fixed argv, no shell
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.SubprocessError, OSError) as e:
            return CheckResult("doc_checks", "failed", f"doc_checks execution failed: {e}")
    if result.returncode != 0:
        return CheckResult("doc_checks", "failed", result.stdout.strip() or result.stderr.strip())
    return CheckResult("doc_checks", "passed")


def docx_rowcheck(docx: bytes, pdf: PdfRenderPort) -> CheckResult:
    """Rowcheck gate for a Word deliverable: fails closed without a converter."""
    return _run_rowcheck(docx, pdf)


def doc_checks(
    data: bytes,
    kind: str,
    account_ids: Sequence[str],
    stale_phrases: Sequence[str],
    excluded_names: Sequence[str],
    verbatim_quotes: Sequence[str] = (),
) -> CheckResult:
    """Document checks gate for Word, deck, or PDF deliverable."""
    return _run_doc_checks(data, kind, account_ids, stale_phrases, excluded_names, verbatim_quotes)


def third_party_scan(data: bytes, kind: str) -> CheckResult:
    """Third-party domain and tool-name scan on final deliverable (docx, pptx, pdf, xlsx)."""
    script = _skill_scripts_dir() / "third_party_scan.py"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        file_path = tmp_path / f"scan.{kind}"
        file_path.write_bytes(data)
        try:
            result = subprocess.run(  # nosec B603 - fixed argv, no shell
                [sys.executable, str(script), str(file_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.SubprocessError, OSError) as e:
            return CheckResult(
                "third_party_scan", "failed", f"third_party_scan execution failed: {e}"
            )
    if result.returncode != 0:
        return CheckResult(
            "third_party_scan", "failed", result.stdout.strip() or result.stderr.strip()
        )
    return CheckResult("third_party_scan", "passed")
