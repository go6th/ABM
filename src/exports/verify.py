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
* ``third_party_scan``: every member of a client file (the five files and the workspace zip)
  scanned for a data tool name or host and for the word "credits"; every external host sorted
  into account and press, data tools (always empty), aggregators and other (spec 12).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import shutil
import signal
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import tempfile
import threading
import zipfile
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from account_intelligence.config import CONFIG_DIR
from account_intelligence.safety.client_output import load_patterns, scrub_text
from account_intelligence.safety.prefilter import cannot_match
from exports.config import provider_hosts, registrable
from exports.ooxml import strip_cached_values
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


def _stream(z: zipfile.ZipFile, name: str, tag: str) -> Iterable[etree._Element]:
    """The elements ``tag`` of one XML member in document order, parsed as a stream: each
    element is handed over complete and dropped when the caller asks for the next one, so a
    document with a million cells is read in constant memory (a whole tree of the same file took
    over a gigabyte at 7,000 postings). Elements are the caller's to read, not to keep."""
    with z.open(name) as fh:
        for _event, el in etree.iterparse(
            fh, events=("end",), tag=tag, resolve_entities=False, no_network=True, huge_tree=True
        ):
            yield el
            el.clear()
            parent = el.getparent()
            # LOOP(read siblings): cap=the children of the parent (each pass deletes one); exit=no
            # earlier sibling is left
            while parent is not None and el.getprevious() is not None:
                del parent[0]  # the siblings already read


def docx_text(data: bytes) -> str:
    """All text of the document body, header and footer (for content checks)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        out = []
        for name in sorted(z.namelist()):
            if name.startswith("word/") and name.endswith(".xml"):
                out.append("".join(t.text or "" for t in _stream(z, name, f"{W}t")))
        return "\n".join(out)


def docx_row_keys(data: bytes) -> tuple[str, ...]:
    """First-cell text of every table row that looks like a row key, in document order."""
    keys = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for tr in _stream(z, "word/document.xml", f"{W}tr"):
            tc = tr.find(f"{W}tc")
            if tc is None:
                continue
            text = "".join(t.text or "" for t in tc.iter(f"{W}t"))
            if _REF.match(text):
                keys.append(text)
    return tuple(keys)


def xlsx_row_keys(data: bytes, sheet: str, column: int = 1) -> tuple[str, ...]:
    """The row keys (the Ref column, ``column`` 1-based) of the data rows of ``sheet``, in row
    order."""
    wb = load_workbook(io.BytesIO(data), read_only=True)
    try:
        ws = wb[sheet]
        keys = []
        for row in ws.iter_rows(min_row=2, max_col=column, values_only=True):
            value = row[column - 1] if row and len(row) >= column else None
            if isinstance(value, str) and _REF.match(value):
                keys.append(value)
        return tuple(keys)
    finally:
        wb.close()


STACK_KEY_SEPARATOR = " | "


def stack_key(business_function: str, category: str, technology: str) -> str:
    """The row key of a Confirmed stack row: its first three cells (the sheet has no Ref column,
    as the reference has none)."""
    return STACK_KEY_SEPARATOR.join((business_function, category, technology))


def xlsx_stack_keys(data: bytes, sheet: str, header_row: int) -> tuple[str, ...]:
    """The row keys of the Confirmed stack rows (``stack_key`` of columns A to C) below the header
    row, in row order. A row with no technology (the none-found note) is not a stack row."""
    wb = load_workbook(io.BytesIO(data), read_only=True)
    try:
        ws = wb[sheet]
        keys = []
        for row in ws.iter_rows(min_row=header_row + 1, max_col=3, values_only=True):
            cells = [("" if v is None else str(v)) for v in row] + ["", "", ""]
            if cells[2]:
                keys.append(stack_key(cells[0], cells[1], cells[2]))
        return tuple(keys)
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


# at most this many LibreOffice conversions run at once in one process (each is a process group
# of its own; the builder runs the checks off the event loop, so two builds can overlap)
_CONVERSIONS = threading.BoundedSemaphore(2)
# resource limits of one conversion: virtual memory (KiB) and CPU seconds, set by a fixed shell
# prologue (no ``preexec_fn``: the converter is started from a worker thread, where a fork hook is
# not safe); the prologue is fixed text, the values are passed as arguments, never interpolated
_LIMIT_AS_KB = 6 * 1024 * 1024
_LIMIT_CPU_S = 240
# LibreOffice's time grows with the XML it parses: a conversion may take ``timeout_s`` plus this
# many seconds per megabyte of the unpacked package, never more than ``MAX_TIMEOUT_S`` (the
# 7,000 posting Jobs workbook needed 133 s against a flat 120 s and its recalculation check
# silently became not_run)
SECONDS_PER_MB = 4.0
MAX_TIMEOUT_S = 900.0
_LIMITED = 'ulimit -v "$1" && ulimit -t "$2" && shift 2 && exec "$@"'
_KILL_GRACE_S = 5.0


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the whole process group of a conversion (``soffice`` starts ``oosplash`` which starts
    ``soffice.bin``: killing the direct child alone leaves the worker running)."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)


class LibreOffice:
    """``PdfRenderPort`` and ``RecalcPort`` over a headless LibreOffice (``soffice``), with a
    timeout, a private profile, a cleared environment, its own process group (killed whole on
    expiry and when the conversion ends), memory and CPU limits and at most one re-run. It is
    synchronous: callers run it off the event loop (``ExportBuilder.build_all`` does).
    ``last_failure`` names why the last conversion produced nothing (``timeout``, ``failed``,
    ``no_output`` or ``not_installed``) so a check that did not run says why."""

    def __init__(self, timeout_s: float = 120.0, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("soffice")
        self.timeout_s = timeout_s
        self.last_failure: str | None = None
        # the time the last conversion was given, for the reason a check did not run
        self.last_timeout_s: float = timeout_s

    def timeout_for(self, body: bytes) -> float:
        """The time one conversion of ``body`` may take (see ``SECONDS_PER_MB``)."""
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                unpacked = sum(i.file_size for i in z.infolist())
        except zipfile.BadZipFile:
            unpacked = len(body)
        return min(
            max(self.timeout_s, MAX_TIMEOUT_S), self.timeout_s + unpacked / 1e6 * SECONDS_PER_MB
        )

    def _argv(
        self, tmp: str, out_dir: Path, src: Path, target: str, cpu_s: int = _LIMIT_CPU_S
    ) -> list[str]:
        assert self.binary is not None
        return [
            "/bin/sh",
            "-c",
            _LIMITED,
            "soffice-limits",
            str(_LIMIT_AS_KB),
            str(cpu_s),
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

    def _run(self, cmd: list[str], tmp: str, timeout_s: float | None = None) -> str | None:
        """Run one conversion in its own process group; None when it exited cleanly, else the
        failure reason. The group is killed on expiry and again when the run is over."""
        env = {"HOME": tmp, "TMPDIR": tmp, "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}
        try:
            proc = subprocess.Popen(  # nosec B603 - fixed argv, no shell string from input
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError:
            return "failed"
        try:
            code = proc.wait(timeout=timeout_s if timeout_s is not None else self.timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            with contextlib.suppress(subprocess.SubprocessError):
                proc.wait(timeout=_KILL_GRACE_S)
            return "timeout"
        finally:
            _kill_group(proc)
        return None if code == 0 else "failed"

    def _convert(self, body: bytes, suffix: str, target: str) -> bytes | None:
        if self.binary is None:
            self.last_failure = "not_installed"
            return None
        self.last_failure = None
        limit = self.timeout_for(body)
        self.last_timeout_s = limit
        cpu_s = max(_LIMIT_CPU_S, int(limit * 1.5))
        # LOOP(converter re-run): cap=2 attempts; exit=converted, None (not_run with reason)
        for _ in range(2):
            with _CONVERSIONS, tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / f"in{suffix}"
                src.write_bytes(body)
                out_dir = Path(tmp) / "out"
                out_dir.mkdir()
                failure = self._run(self._argv(tmp, out_dir, src, target, cpu_s), tmp, limit)
                if failure is not None:
                    self.last_failure = failure
                    if failure == "timeout":
                        return None  # a second attempt would only hold the worker twice as long
                    continue
                produced = sorted(out_dir.glob("in.*"))
                if produced:
                    self.last_failure = None
                    return produced[0].read_bytes()
                self.last_failure = "no_output"
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


def _not_run_reason(converter: object, absent: str, what: str) -> str:
    """Why a conversion check did not run: the converter's own reason (a timeout, a failure) or
    ``absent`` (none is installed)."""
    why = getattr(converter, "last_failure", None)
    if why in (None, "not_installed"):
        return absent
    if why == "timeout":
        seconds = getattr(converter, "last_timeout_s", None)
        if seconds:
            return f"the {what} conversion did not finish (timeout after {seconds:.0f} s)"
    return f"the {what} conversion did not finish ({why})"


def pdf_row_presence(docx: bytes, keys: Sequence[str], pdf: object) -> CheckResult:
    """Every row key once and in order in the PDF text (``not_run`` without a converter)."""
    text = pdf.pdf_text(docx)  # type: ignore[attr-defined]
    if text is None:
        reason = _not_run_reason(pdf, "no Word to PDF converter installed", "Word to PDF")
        return CheckResult("pdf_row_presence", "not_run", reason)
    problems = keys_once_in_order(text, keys)
    return CheckResult("pdf_row_presence", "failed" if problems else "passed", details=problems)


def recalc_check(
    xlsx: bytes, expected: Mapping[tuple[str, str], int | str | float], recalc: object
) -> CheckResult:
    """Zero error values and each formula cell (sheet, cell) equal to its code-computed value:
    a count, a share (within rounding) or a rank letter."""
    # the stored results are taken out first: LibreOffice keeps a stored result as it is, so a
    # file that carries its values would only be read back, never calculated
    try:
        bare = strip_cached_values(xlsx)
    except zipfile.BadZipFile:
        bare = xlsx
    done = recalc.recalculated(bare)  # type: ignore[attr-defined]
    if done is None:
        reason = _not_run_reason(recalc, "no spreadsheet engine installed", "spreadsheet")
        return CheckResult("workbook_recalc", "not_run", reason)
    wb = load_workbook(io.BytesIO(done), data_only=True)
    problems: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value in _ERRORS:
                    problems.append(f"{ws.title}!{cell.coordinate}: {cell.value}")
    for (sheet, coord), value in expected.items():
        got = wb[sheet][coord].value
        if isinstance(value, float) and isinstance(got, int | float) and not isinstance(got, bool):
            if abs(float(got) - value) > 1e-9:
                problems.append(f"{sheet}!{coord}: {got!r} != {value}")
        elif got != value:
            problems.append(f"{sheet}!{coord}: {got!r} != {value}")
    return CheckResult(
        "workbook_recalc", "failed" if problems else "passed", details=tuple(problems)
    )


def cached_values_check(
    xlsx: bytes, expected: Mapping[tuple[str, str], int | str | float]
) -> CheckResult:
    """Every formula cell of the plan carries its result in the delivered file, equal to the
    value computed in code (what a viewer that does not calculate shows)."""
    problems: list[str] = []
    by_sheet: dict[str, dict[str, int | str | float]] = {}
    for (sheet, coord), value in expected.items():
        by_sheet.setdefault(sheet, {})[coord] = value
    wb = load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    try:
        for sheet, wanted in by_sheet.items():
            if sheet not in wb.sheetnames:
                problems.extend(f"{sheet}!{c}: sheet missing" for c in sorted(wanted))
                continue
            spots = {c: coordinate_to_tuple(c) for c in wanted}
            rows = [r for r, _c in spots.values()]
            cols = [c for _r, c in spots.values()]
            first_row, first_col = min(rows), min(cols)
            grid = list(
                wb[sheet].iter_rows(
                    min_row=first_row,
                    max_row=max(rows),
                    min_col=first_col,
                    max_col=max(cols),
                    values_only=True,
                )
            )
            seen: dict[str, object] = {}
            for coord, (r, c) in spots.items():
                line = grid[r - first_row] if r - first_row < len(grid) else ()
                seen[coord] = line[c - first_col] if c - first_col < len(line) else None
            for coord, value in sorted(wanted.items()):
                got = seen.get(coord)
                if isinstance(value, float):
                    ok = isinstance(got, int | float) and abs(float(got) - value) <= 1e-9
                elif value == "":
                    ok = got in (None, "")
                else:
                    ok = got == value
                if not ok:
                    problems.append(f"{sheet}!{coord}: cached {got!r} != {value!r}")
    finally:
        wb.close()
    return CheckResult(
        "workbook_cached_values", "failed" if problems else "passed", details=tuple(problems)
    )


def _skill_scripts_dir() -> Path:
    """Directory containing the skill's gate scripts (rowcheck.py, doc_checks.py)."""
    return CONFIG_DIR / "skill" / "archive" / "scripts"


def _run_rowcheck(docx: bytes, pdf: PdfRenderPort) -> CheckResult:
    """Run the skill's rowcheck.py on the docx and its PDF rendering.

    A row the script finds lost fails the check. A converter or tool that is unavailable, times
    out or cannot be run leaves the check ``not_run`` with its reason (listed in the manifest and
    logged), as the PDF row presence check does: an infrastructure failure is not a lost row, and
    used to end a finished research with no files (this check failed closed on a timeout).
    """
    pdf_bytes = pdf.pdf_bytes(docx)
    if pdf_bytes is None:
        return CheckResult(
            "docx_rowcheck",
            "not_run",
            "Word to PDF converter unavailable ("
            f"{getattr(pdf, 'last_failure', None) or 'not installed or failed'})",
        )
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return CheckResult("docx_rowcheck", "not_run", "pdftotext not installed")
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
            return CheckResult(
                "docx_rowcheck", "not_run", f"rowcheck could not run ({type(e).__name__})"
            )
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
                    # one line per paragraph: joined without a break, a cell ending "S-0002"
                    # and the heading "6. People" read as the citation S-00026 (2026-10-25);
                    # read as a stream, so a very large table is not held as a tree
                    parts.append(
                        "\n".join(
                            "".join(t.text or "" for t in p.iter(f"{W}t"))
                            for p in _stream(z, name, f"{W}p")
                        )
                    )
                elif name in ("docProps/core.xml", "docProps/app.xml"):
                    root = etree.fromstring(z.read(name), parser=_PARSER)
                    parts.append("".join(t.text or "" for t in root.iter()))
                elif name.endswith((".md", ".txt")):
                    parts.append(z.read(name).decode("utf-8", "ignore"))
            return "\n".join(parts)
    except zipfile.BadZipFile:
        return data.decode("utf-8", "ignore")


# A cited source code as the registry writes it (``S-0001``, widening past four digits). The
# archived skill script matched ``[SJU]\d{1,3}``, which no real code ever satisfies and which
# "Amazon S3" or "J1 visa" does; such text failed builds for nothing (staging 2026-10-21).
_CITATION = re.compile(r"\bS-\d{4,}\b")
_SPACED_HYPHEN = re.compile(r"\S\s+-\s+\S")
# What an exempt passage is replaced by before the dash scan: a non-space letter, so removing a
# passage can neither create a spaced hyphen nor join two others
_BLANK = "X"


def _dashes_in(text: str, dash_characters: Sequence[str], spaced: bool) -> int:
    n = sum(text.count(ch) for ch in dash_characters)
    return n + (len(_SPACED_HYPHEN.findall(text)) if spaced else 0)


def dash_count_plain(
    text: str, exempt: Sequence[str], dash_characters: Sequence[str], spaced: bool
) -> int:
    """The banned dashes of ``text`` once every exempt passage (ascending by length) is blanked,
    longest first so a title inside a quote cannot split it: the rule as first written, one
    replacement over the whole text per passage."""
    authored = text
    for passage in reversed(exempt):
        authored = authored.replace(passage, _BLANK)
    return _dashes_in(authored, dash_characters, spaced)


# a passage key: its first characters (the whole passage when it is shorter)
_KEY = 6


def dash_count(
    text: str, exempt: Sequence[str], dash_characters: Sequence[str], spaced: bool
) -> int:
    """``dash_count_plain`` in time that does not grow with the passages times the text. A
    document of 30,000 table rows has thousands of exempt titles; one replacement of each over a
    100 MB text took half a minute (7,000 postings, fix wave C). Blanking a passage can only
    remove a dash, never make one, when the passage neither starts nor ends in a space and holds
    no line break; so only the lines that hold a dash (or take part in a spaced hyphen) need their
    passages blanked, each found by its first characters. Passages that do have a space at an end
    or a line break take the plain path."""
    if not exempt:
        return _dashes_in(text, dash_characters, spaced)
    if any(p != p.strip() or "\n" in p for p in exempt):
        return dash_count_plain(text, exempt, dash_characters, spaced)
    lines = text.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    touched: set[int] = set()
    for ch in dash_characters:
        at = text.find(ch)
        # LOOP(dash positions): cap=the text length (each find starts after the last hit); exit=no
        # further occurrence
        while at != -1:
            touched.add(bisect_right(offsets, at) - 1)
            at = text.find(ch, at + 1)
    if spaced:
        for m in _SPACED_HYPHEN.finditer(text):
            first, last = (
                bisect_right(offsets, m.start()) - 1,
                bisect_right(offsets, m.end() - 1) - 1,
            )
            touched.update(range(first, last + 1))
    if not touched:
        return 0
    # first characters -> passage length -> the passages: a position is checked by one set
    # lookup per length that prefix has, however many passages share it
    keyed: dict[str, dict[int, set[str]]] = {}
    for p in exempt:
        keyed.setdefault(p[:_KEY], {}).setdefault(len(p), set()).add(p)
    widths = sorted({len(k) for k in keyed})
    begins = {k[:2] for k in keyed}  # a position that starts no passage is skipped at once
    for i in touched:
        line = lines[i]
        found = {
            candidate
            for at in range(len(line))
            if line[at : at + 2] in begins
            for width in widths
            for size, group in keyed.get(line[at : at + width], {}).items()
            if (candidate := line[at : at + size]) in group
        }
        for passage in sorted(found, key=len, reverse=True):
            line = line.replace(passage, _BLANK)
        lines[i] = line
    total = 0
    run: list[int] = []
    for i in sorted(touched) + [-1]:
        if run and i != run[-1] + 1:  # a run of neighbouring lines is counted together
            region = "\n".join(lines[run[0] : run[-1] + 1])
            total += _dashes_in(region, dash_characters, spaced)
            run = []
        run.append(i)
    return total


def _run_doc_checks(
    data: bytes,
    kind: str,
    account_ids: Sequence[str],
    stale_phrases: Sequence[str],
    excluded_names: Sequence[str],
    verbatim_quotes: Sequence[str],
    passthrough: Sequence[str] = (),
    dash_characters: Sequence[str] = ("—", "–"),
    spaced_hyphen_is_punctuation: bool = True,
) -> CheckResult:
    """The document checks over the built file's extracted text (one implementation of the
    skill's ``doc_checks.py`` rules, with its two defects fixed, see ``doc_checks``).

    The dash rule's scope is authored prose (``export_rules.yaml banned_dashes.scope``): verbatim
    quotes and ``passthrough`` texts (stored titles, names, labels and links the file prints as
    they are) are blanked before the scan. ``details`` carry rule codes and counts, never text."""
    text = _extract_doc_text(data)
    details: list[str] = []
    cited = set(_CITATION.findall(text))
    orphans = sorted(cited - set(account_ids))
    details.extend(f"orphan_citation:{code}" for code in orphans)

    exempt = sorted({t for t in (*verbatim_quotes, *passthrough) if len(t) > 1}, key=len)
    dashes = dash_count(text, exempt, dash_characters, spaced_hyphen_is_punctuation)
    if dashes:
        details.append(f"banned_dash:{dashes}")

    for rule, items in (("stale_phrase", stale_phrases), ("excluded_name", excluded_names)):
        for i, item in enumerate(items, start=1):
            n = text.count(item) if item else 0
            if n:
                details.append(f"{rule}:#{i}x{n}")  # the index, never the phrase or the name

    if not details:
        return CheckResult("doc_checks", "passed")
    rules = sorted({d.split(":", 1)[0] for d in details})
    return CheckResult("doc_checks", "failed", f"{kind}: {', '.join(rules)}", tuple(details))


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
    passthrough: Sequence[str] = (),
    dash_characters: Sequence[str] = ("—", "–"),
    spaced_hyphen_is_punctuation: bool = True,
) -> CheckResult:
    """Document checks gate for a Word, deck or PDF deliverable: no orphan citation, no banned
    dash in authored prose, no stale phrase, no excluded name. Until 2026-10-21 this ran the
    archived skill script, which scanned every dash in the file (a fifth of stored job titles
    carry one) and matched no real citation; the rules now run here, in process."""
    return _run_doc_checks(
        data,
        kind,
        account_ids,
        stale_phrases,
        excluded_names,
        verbatim_quotes,
        passthrough,
        dash_characters,
        spaced_hyphen_is_punctuation,
    )


# every external host a client file links or names: the URL form in text and relationships
_DOMAIN = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})", re.IGNORECASE)
# the word itself, any case: a client file never talks about tool credits (spec 12)
_CREDITS = re.compile(r"\bcredits\b", re.IGNORECASE)
# the schema and namespace hosts every Office package carries
_SCHEMA_HOSTS = ("openxmlformats.org", "microsoft.com", "w3.org", "purl.org")
GROUPS = ("account_and_press", "data_tools", "aggregators", "other")


@dataclass(frozen=True)
class ThirdPartyReport:
    """What the third-party scan found: the check and every external host by group."""

    result: CheckResult
    domains: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _in(host: str, hosts: Iterable[str]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts if h)


def third_party_word(text: str) -> str | None:
    """``cost_word`` or ``data_tool_host`` when ``text`` carries what the third-party scan refuses
    beyond the client-output denylist (the word the providers use for their own unit, or a link
    to a data tool's host), else None. The rule is named ``cost_word``, never by the word: the
    manifest lists rule names and is itself scanned for that word. The denylist's own patterns
    are ``leak_hits``' and are not tested here."""
    if not cannot_match(_CREDITS, text) and _CREDITS.search(text):
        return "cost_word"
    if "." in text:
        tools = provider_hosts()
        for m in _DOMAIN.finditer(text):
            host = m.group(1).casefold().removeprefix("www.")
            if not _in(host, _SCHEMA_HOSTS) and (_in(host, tools) or registrable(host) in tools):
                return "data_tool_host"
    return None


@lru_cache(maxsize=1)
def _tool_url() -> re.Pattern[str] | None:
    """A link to a data tool's host (``config/providers.yaml``), through to the next space."""
    hosts = sorted(provider_hosts(), key=len, reverse=True)
    if not hosts:
        return None
    joined = "|".join(re.escape(h) for h in hosts)
    return re.compile(rf"https?://(?:[a-z0-9-]+\.)*(?:{joined})(?![a-z0-9-])\S*", re.IGNORECASE)


def scrub_member(text: str, restricted: Sequence[str], marker: str) -> tuple[str, int]:
    """``(text, n)``: a workspace member's text with every restricted term, data tool name, link
    to a data tool and the word "credits" replaced by ``marker``, and the replacements counted.
    For the run's own records (pull log, searches, detections), which may mention what a client
    file may not print: the member stays in the zip, scrubbed, and the zip scans clean."""
    text, n = scrub_text(text, restricted, marker)
    pattern = _tool_url()
    if pattern is not None and "://" in text:
        text, hosts = pattern.subn(marker, text)
        n += hosts
    if not cannot_match(_CREDITS, text):
        text, words = _CREDITS.subn(marker, text)
        n += words
    return text, n


def third_party_blocked(text: str) -> str | None:
    """The reason the third-party scan would refuse ``text`` in a client file (``tool_name``,
    ``cost_word`` or ``data_tool_host``), or None. A text copied from a page, such as an archived
    copy, is checked with this before it is put in the workspace zip, so one page that says the
    word leaves that page out and never stops the delivery."""
    if any(not cannot_match(p, text) and p.search(text) for p in load_patterns()):
        return "tool_name"
    return third_party_word(text)


def third_party_scan(
    data: bytes,
    kind: str,
    *,
    account_hosts: Sequence[str] = (),
    aggregator_hosts: Sequence[str] = (),
    scanned: frozenset[str] = frozenset(),
) -> ThirdPartyReport:
    """Spec 12 on one client file (docx, xlsx or the workspace zip): every member's text is
    scanned for a data tool name (the client-output denylist), a data tool host
    (``config/providers.yaml``) and the word "credits"; any hit fails. Every external host is
    sorted into account and press (``account_hosts``: the account domain, its subdomains, the
    verified careers host and the hosts of its primary sources), data tools, aggregators
    (``aggregator_hosts``) and other. Details name patterns and hosts, never text. ``scanned``
    holds SHA-256 digests of nested files already scanned on their own (their hosts are in the
    caller's report already), as for ``scan_package``."""
    tools = provider_hosts()
    patterns = load_patterns()
    hits: list[str] = []
    found: dict[str, set[str]] = {g: set() for g in GROUPS}
    for where, body in _members(data, scanned=scanned):
        if where.endswith(" (name)"):
            continue
        text = body.decode("utf-8", "ignore")
        for p in patterns:
            if not cannot_match(p, text) and p.search(text):
                hits.append(f"{where}: tool_name")
                break
        if not cannot_match(_CREDITS, text) and _CREDITS.search(text):
            hits.append(f"{where}: credits")
        for m in _DOMAIN.finditer(text):
            host = m.group(1).casefold().removeprefix("www.")
            if _in(host, _SCHEMA_HOSTS):
                continue
            if _in(host, tools) or registrable(host) in tools:
                found["data_tools"].add(host)
            elif _in(host, account_hosts):
                found["account_and_press"].add(host)
            elif _in(host, aggregator_hosts):
                found["aggregators"].add(host)
            else:
                found["other"].add(host)
    hits.extend(f"data_tool_host: {h}" for h in sorted(found["data_tools"]))
    domains = {g: tuple(sorted(found[g])) for g in GROUPS}
    if hits:
        return ThirdPartyReport(
            CheckResult("third_party_scan", "failed", f"{kind}: {len(hits)} hits", tuple(hits)),
            domains,
        )
    return ThirdPartyReport(CheckResult("third_party_scan", "passed"), domains)
