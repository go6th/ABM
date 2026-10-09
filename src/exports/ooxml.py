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
from datetime import date
from xml.sax.saxutils import escape  # nosec B406 -- escapes generated XML; does not parse input.

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
