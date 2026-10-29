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

import hashlib
import io
import re
import shutil
import subprocess  # nosec B404 - fixed argv, no shell
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree
from openpyxl import load_workbook

from account_intelligence.config import CONFIG_DIR
from account_intelligence.safety.client_output import load_patterns
from exports.config import provider_hosts, registrable
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
    xlsx: bytes, expected: Mapping[tuple[str, str], int | str | float], recalc: object
) -> CheckResult:
    """Zero error values and each formula cell (sheet, cell) equal to its code-computed value:
    a count, a share (within rounding) or a rank letter."""
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
        if isinstance(value, float) and isinstance(got, int | float) and not isinstance(got, bool):
            if abs(float(got) - value) > 1e-9:
                problems.append(f"{sheet}!{coord}: {got!r} != {value}")
        elif got != value:
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
                    # one line per paragraph: joined without a break, a cell ending "S-0002"
                    # and the heading "6. People" read as the citation S-00026 (2026-10-25)
                    parts.append(
                        "\n".join(
                            "".join(t.text or "" for t in p.iter(f"{W}t"))
                            for p in root.iter(f"{W}p")
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

    authored = text
    exempt = sorted({t for t in (*verbatim_quotes, *passthrough) if len(t) > 1}, key=len)
    for passage in reversed(exempt):  # longest first, so a title inside a quote cannot split it
        authored = authored.replace(passage, _BLANK)
    dashes = sum(authored.count(ch) for ch in dash_characters)
    if spaced_hyphen_is_punctuation:
        dashes += len(_SPACED_HYPHEN.findall(authored))
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


def third_party_scan(
    data: bytes,
    kind: str,
    *,
    account_hosts: Sequence[str] = (),
    aggregator_hosts: Sequence[str] = (),
) -> ThirdPartyReport:
    """Spec 12 on one client file (docx, xlsx or the workspace zip): every member's text is
    scanned for a data tool name (the client-output denylist), a data tool host
    (``config/providers.yaml``) and the word "credits"; any hit fails. Every external host is
    sorted into account and press (``account_hosts``: the account domain, its subdomains, the
    verified careers host and the hosts of its primary sources), data tools, aggregators
    (``aggregator_hosts``) and other. Details name patterns and hosts, never text."""
    tools = provider_hosts()
    patterns = load_patterns()
    hits: list[str] = []
    found: dict[str, set[str]] = {g: set() for g in GROUPS}
    for where, body in _members(data):
        text = body.decode("utf-8", "ignore")
        if where.endswith(" (name)"):
            continue
        for p in patterns:
            if p.search(text):
                hits.append(f"{where}: tool_name")
                break
        if _CREDITS.search(text):
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
