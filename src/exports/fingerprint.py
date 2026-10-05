"""Style and layout fingerprints of docx and xlsx files: the "same look" test (spec 9.4). Pure
(bytes in, a canonical dict out; no I/O beyond reading the given bytes).

A fingerprint is the set of DISTINCT style atoms a file uses, with every text node and every
count removed, so two accounts with different content but the same look have equal fingerprints,
and an account with empty sections has a subset (it uses fewer atoms, never different ones).

* docx: page size and margins; the distinct run, paragraph, table, grid, row and cell property
  elements of the body, header and footer; the styles part.
* xlsx: the stylesheet (fonts, fills, borders, number formats, cell formats); per sheet its name,
  column widths, frozen pane, filter presence, page setup and print options.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Any

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


def _xml(z: zipfile.ZipFile, name: str) -> Any:
    return etree.fromstring(z.read(name), parser=_PARSER)


def _canon(el: Any) -> str:
    """Canonical XML of an element with text and tails removed (structure and attributes only)."""
    clone = etree.fromstring(etree.tostring(el), parser=_PARSER)
    for node in clone.iter():
        node.text = None
        node.tail = None
    return etree.tostring(clone, method="c14n").decode()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def docx_fingerprint(data: bytes) -> dict[str, Any]:
    """Fingerprint of a Word document (see the module docstring)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        doc = _xml(z, "word/document.xml")
        parts = [doc] + [
            _xml(z, n)
            for n in sorted(z.namelist())
            if n.startswith(("word/header", "word/footer")) and n.endswith(".xml")
        ]
        styles = _canon(_xml(z, "word/styles.xml"))
    atoms: dict[str, set[str]] = {
        k: set() for k in ("rPr", "pPr", "tblPr", "tblGrid", "trPr", "tcPr")
    }
    for part in parts:
        for tag in atoms:
            for el in part.iter(f"{W}{tag}"):
                atoms[tag].add(_canon(el))
    sect = doc.find(f".//{W}sectPr")
    page = {}
    if sect is not None:
        for tag in ("pgSz", "pgMar"):
            el = sect.find(f"{W}{tag}")
            page[tag] = dict(sorted(el.attrib.items())) if el is not None else {}
    return {
        "kind": "docx",
        "page": page,
        "styles": _digest(styles),
        **{k: sorted(_digest(v) for v in vals) for k, vals in atoms.items()},
    }


def xlsx_fingerprint(data: bytes) -> dict[str, Any]:
    """Fingerprint of a workbook (see the module docstring)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        styles = _xml(z, "xl/styles.xml")
        book = _xml(z, "xl/workbook.xml")
        rels = _xml(z, "xl/_rels/workbook.xml.rels")
        targets = {r.get("Id"): r.get("Target") for r in rels}
        sheets: list[dict[str, Any]] = []
        for sh in book.iter(f"{S}sheet"):
            rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = str(targets[rid]).lstrip("/")
            path = target if target.startswith("xl/") else f"xl/{target}"
            ws = _xml(z, path)
            pane = ws.find(f".//{S}pane")
            setup = ws.find(f"{S}pageSetup")
            options = ws.find(f"{S}printOptions")
            props = ws.find(f"{S}sheetPr")
            sheets.append(
                {
                    "name": sh.get("name"),
                    "cols": [
                        (c.get("min"), c.get("max"), c.get("width"), c.get("customWidth"))
                        for c in ws.iter(f"{S}col")
                    ],
                    "pane": dict(sorted(pane.attrib.items())) if pane is not None else {},
                    "filter": ws.find(f"{S}autoFilter") is not None,
                    "setup": dict(sorted(setup.attrib.items())) if setup is not None else {},
                    "print": dict(sorted(options.attrib.items())) if options is not None else {},
                    "sheet_props": _canon(props) if props is not None else "",
                }
            )
    sections = {}
    for tag in ("numFmts", "fonts", "fills", "borders", "cellXfs", "cellStyleXfs", "cellStyles"):
        el = styles.find(f"{S}{tag}")
        sections[tag] = sorted(_digest(_canon(child)) for child in el) if el is not None else []
    return {"kind": "xlsx", "styles": sections, "sheets": sheets}


def is_subset(small: dict[str, Any], big: dict[str, Any]) -> bool:
    """True when every style atom of ``small`` is in ``big`` (an account with empty sections uses
    a subset of the look, never a different one). Page and sheet layout must match exactly."""
    if small.get("kind") != big.get("kind"):
        return False
    if small["kind"] == "docx":
        if small["page"] != big["page"] or small["styles"] != big["styles"]:
            return False
        return all(
            set(small[k]) <= set(big[k]) for k in ("rPr", "pPr", "tblPr", "tblGrid", "trPr", "tcPr")
        )
    if [s["name"] for s in small["sheets"]] != [s["name"] for s in big["sheets"]]:
        return False
    return all(set(small["styles"][k]) <= set(big["styles"][k]) for k in small["styles"])
