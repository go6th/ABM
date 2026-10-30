"""Deterministic Office Open XML packages (B2, B10).

Renderers (openpyxl, python-docx, and the Node renderer once ported) stamp the wall clock into
zip entry times and ``docProps/core.xml`` and their own tool name into ``docProps/app.xml``.
``normalise_package`` rebuilds the package so equal content gives equal bytes:

* ``[Content_Types].xml`` first, every other member in sorted order;
* a fixed entry time, compression, permissions and creator system on every member;
* ``docProps/core.xml`` and ``docProps/app.xml`` replaced by fixed documents carrying only the
  title, the configured author and company and the run's ``as_of`` date (no machine, user or
  tool name, no wall-clock time);
* thumbnails, custom properties and custom XML parts dropped (with their relationships and
  content-type overrides).

Verified against openpyxl 3.1.5 (``writer/excel.py`` sets ``modified`` to now on save) and
python-docx 1.2.0 (template core properties carry its own author and dates).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping
from datetime import date
from xml.sax.saxutils import escape  # nosec B406 -- escapes generated XML; does not parse input.

from lxml import etree

CONTENT_TYPES = "[Content_Types].xml"
CORE = "docProps/core.xml"
APP = "docProps/app.xml"
_DROP = ("docProps/thumbnail", "docProps/custom.xml", "customXml/")
_REL = re.compile(r"<Relationship\b[^>]*/>")
_TARGET = re.compile(r'Target="([^"]*)"')


def core_xml(title: str, author: str, as_of: date) -> bytes:
    """The fixed core properties document."""
    stamp = f"{as_of.isoformat()}T00:00:00Z"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms='
        '"http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(title)}</dc:title><dc:creator>{escape(author)}</dc:creator>"
        f"<cp:lastModifiedBy>{escape(author)}</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
        "</cp:coreProperties>"
    ).encode()


def app_xml(company: str) -> bytes:
    """The fixed extended properties document (no application name)."""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        'extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'docPropsVTypes"><Company>{escape(company)}</Company></Properties>'
    ).encode()


def _dropped(name: str) -> bool:
    return name.startswith(_DROP)


def _target(rel: str) -> str:
    found = _TARGET.search(rel)
    path = found.group(1) if found else ""
    # LOOP(relationship target prefix strip): cap=the target's length (each pass removes at
    # least one character); exit=no leading "../" or "/" left
    while path.startswith(("../", "/")):
        path = path[3:] if path.startswith("../") else path[1:]
    return path


def _strip_refs(xml: bytes, names: list[str]) -> bytes:
    """Remove relationships and content-type overrides that point at dropped members."""
    text = xml.decode("utf-8")
    for name in names:
        text = re.sub(rf'<Override\b[^>]*PartName="/{re.escape(name)}"[^>]*/>', "", text)
    return _REL.sub(lambda m: "" if _target(m.group(0)) in names else m.group(0), text).encode()


def normalise_package(
    data: bytes,
    *,
    title: str,
    author: str,
    company: str,
    as_of: date,
    entry_time: tuple[int, int, int, int, int, int],
    compression: str = "deflate",
) -> bytes:
    """Rebuild an OOXML package deterministically (see the module docstring)."""
    method = zipfile.ZIP_DEFLATED if compression == "deflate" else zipfile.ZIP_STORED
    with zipfile.ZipFile(io.BytesIO(data)) as src:
        members = {i.filename: src.read(i) for i in src.infolist() if not i.is_dir()}
    dropped = sorted(n for n in members if _dropped(n))
    for name in dropped:
        del members[name]
    members[CORE] = core_xml(title, author, as_of)
    members[APP] = app_xml(company)
    if dropped:
        for name in list(members):
            if name == CONTENT_TYPES or name.endswith(".rels"):
                members[name] = _strip_refs(members[name], dropped)
    return _pack(members, entry_time, method)


def _pack(
    members: Mapping[str, bytes], entry_time: tuple[int, int, int, int, int, int], method: int
) -> bytes:
    """Write ``members`` as one package: ``[Content_Types].xml`` first, the rest sorted, every
    entry with the fixed time, compression and attributes."""
    order = [CONTENT_TYPES] + sorted(n for n in members if n != CONTENT_TYPES)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for name in order:
            info = zipfile.ZipInfo(name, date_time=entry_time)
            info.compress_type = method
            info.create_system = 0
            info.external_attr = 0o600 << 16
            dst.writestr(info, members[name], compress_type=method, compresslevel=6)
    return out.getvalue()


# ---- cached formula values -----------------------------------------------------------------
# openpyxl writes a formula cell as ``<c r="B7" s="3"><f>...</f><v></v></c>`` with no result, so a
# viewer that does not calculate (a mail or drive preview, pandas, a phone) shows nothing. The
# builder knows every formula's value from the same plan that writes it, so the value is put in
# the cell here (``<v>`` and, for text, ``t="str"``): the formula text, the styles and every
# other part stay byte for byte as written, and Excel and LibreOffice still recalculate on load
# (``fullCalcOnLoad``). A byte-exact, deterministic step: no engine, no clock.

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_FORMULA_CELL = re.compile(
    r'<c r="(?P<ref>[A-Z]+[0-9]+)"(?P<attrs>[^>]*)>(?P<f><f>[^<]*</f>)(?:<v\s*/>|<v></v>)</c>'
)
_CACHED_CELL = re.compile(
    r'<c r="(?P<ref>[A-Z]+[0-9]+)"(?P<attrs>[^>]*)>(?P<f><f>[^<]*</f>)<v>[^<]*</v></c>'
)
_TYPE_ATTR = re.compile(r'\s+t="[^"]*"')
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _sheet_parts(members: Mapping[str, bytes]) -> dict[str, str]:
    """``{sheet name: member name}`` from the workbook part and its relationships."""
    book = etree.fromstring(members["xl/workbook.xml"], parser=_PARSER)
    rels = etree.fromstring(members["xl/_rels/workbook.xml.rels"], parser=_PARSER)
    target = {
        r.get("Id"): str(r.get("Target")) for r in rels.iter(f"{{{_NS_PKG_REL}}}Relationship")
    }
    out: dict[str, str] = {}
    for sheet in book.iter(f"{{{_NS_MAIN}}}sheet"):
        path = target.get(sheet.get(f"{{{_NS_REL}}}id"))
        name = sheet.get("name")
        if path and name:
            out[name] = path.lstrip("/") if path.startswith("/") else f"xl/{path}"
    return out


def _value_xml(value: int | float | str) -> tuple[str, str]:
    """``(type attribute, value text)`` of a cached result."""
    if isinstance(value, bool):
        return ' t="b"', "1" if value else "0"
    if isinstance(value, int):
        return "", str(value)
    if isinstance(value, float):
        return "", repr(value)
    return ' t="str"', escape(value)


def cache_formula_values(
    data: bytes,
    values: Mapping[tuple[str, str], int | float | str],
    *,
    entry_time: tuple[int, int, int, int, int, int],
    compression: str = "deflate",
) -> tuple[bytes, int, tuple[str, ...]]:
    """``(package, cached, missing)``: ``data`` with the result of every formula cell named in
    ``values`` (``(sheet, coordinate)`` to value) stored beside its formula, the number of cells
    given a value, and the formula cells left without one (a formula the plan does not know)."""
    method = zipfile.ZIP_DEFLATED if compression == "deflate" else zipfile.ZIP_STORED
    with zipfile.ZipFile(io.BytesIO(data)) as src:
        members = {i.filename: src.read(i) for i in src.infolist() if not i.is_dir()}
    parts = _sheet_parts(members)
    by_sheet: dict[str, dict[str, int | float | str]] = {}
    for (sheet, coord), value in values.items():
        by_sheet.setdefault(sheet, {})[coord] = value
    cached = 0
    missing: list[str] = []
    for sheet, wanted in by_sheet.items():
        part = parts.get(sheet)
        if part is None or part not in members:
            missing.extend(f"{sheet}!{c}" for c in sorted(wanted))
            continue
        done: set[str] = set()

        def put(
            m: re.Match[str], wanted: dict[str, int | float | str] = wanted, done: set[str] = done
        ) -> str:
            value = wanted.get(m.group("ref"))
            if value is None:
                return m.group(0)
            kind, text = _value_xml(value)
            done.add(m.group("ref"))
            attrs = _TYPE_ATTR.sub("", m.group("attrs"))
            return f'<c r="{m.group("ref")}"{attrs}{kind}>{m.group("f")}<v>{text}</v></c>'

        members[part] = _FORMULA_CELL.sub(put, members[part].decode("utf-8")).encode("utf-8")
        cached += len(done)
        missing.extend(f"{sheet}!{c}" for c in sorted(set(wanted) - done))
    return _pack(members, entry_time, method), cached, tuple(missing)


def strip_cached_values(data: bytes) -> bytes:
    """``data`` with the stored result of every formula cell removed again (so a spreadsheet
    engine must calculate it: LibreOffice keeps a stored result as it is)."""
    with zipfile.ZipFile(io.BytesIO(data)) as src:
        infos = [i for i in src.infolist() if not i.is_dir()]
        members = {i.filename: src.read(i) for i in infos}
    changed = False
    for name, body in members.items():
        if not name.startswith("xl/worksheets/") or b"<f>" not in body:
            continue
        text = _CACHED_CELL.sub(
            lambda m: (
                f'<c r="{m.group("ref")}"{_TYPE_ATTR.sub("", m.group("attrs"))}>'
                f"{m.group('f')}<v></v></c>"
            ),
            body.decode("utf-8"),
        )
        members[name] = text.encode("utf-8")
        changed = True
    if not changed:
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in infos:
            dst.writestr(info, members[info.filename], compress_type=info.compress_type)
    return out.getvalue()
