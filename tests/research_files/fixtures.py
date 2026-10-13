"""Small SYNTHETIC files of every supported type, built in code, plus a corrupt copy of each."""

from __future__ import annotations

import gzip
import io
import json
import struct
import tarfile
import wave
import zipfile

ROWS = [
    ["id", "name", "revenue", "joined", "email"],
    ["1", "Acme Example", "1200.5", "2024-01-05", "ann@example.com"],
    ["2", "Beta Example", "980", "2024-02-11", ""],
    ["3", "Gamma Example", "n/a", "05/03/2024", ""],
    ["2", "Beta Example", "980", "2024-02-11", ""],
]
DOC_TEXT = (
    "Revenue grew 40% in 2024 according to the annual report [1]. "
    "Revenue grew 25% last year. The company has 120 employees. "
    "Headcount of 150 was reported later."
)


def csv_bytes(rows: list[list[str]] = ROWS, sep: str = ",") -> bytes:
    return ("\n".join(sep.join(r) for r in rows) + "\n").encode()


def xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Accounts"
    for row in ROWS:
        sheet.append([float(c) if c.replace(".", "", 1).isdigit() else c for c in row])
    second = book.create_sheet("Empty")
    del second
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def parquet_bytes() -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({"id": [1, 2, 3], "name": ["Acme", "Beta", "Gamma"], "value": [1.5, 2.0, 2.5]})
    out = io.BytesIO()
    pq.write_table(table, out, row_group_size=2)
    return out.getvalue()


def json_bytes() -> bytes:
    return json.dumps([dict(zip(ROWS[0], r, strict=True)) for r in ROWS[1:]]).encode()


def jsonl_bytes() -> bytes:
    lines = [json.dumps(dict(zip(ROWS[0], r, strict=True))) for r in ROWS[1:]]
    return ("\n".join(lines[:2] + ["{not json"] + lines[2:]) + "\n").encode()


def pdf_bytes(pages: list[str] | None = None) -> bytes:
    """A minimal valid PDF with one text line per page (Helvetica)."""
    pages = pages or [DOC_TEXT[:90], DOC_TEXT[90:]]
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R"
            f" /Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        safe = text.replace("(", "").replace(")", "")
        stream = f"BT /F1 10 Tf 40 700 Td ({safe}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % n + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    )
    return out.getvalue()


def docx_bytes() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("Overview", level=1)
    doc.add_paragraph(DOC_TEXT)
    doc.add_heading("Empty section", level=1)
    doc.add_heading("Team", level=1)
    doc.add_paragraph("Contact the team at team@example.com for details.")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def pptx_bytes() -> bytes:
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for n, (title, body) in enumerate([("Plan", DOC_TEXT), ("Next steps", "Ship it.")], 1):
            z.writestr(
                f"ppt/slides/slide{n}.xml",
                f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree>'
                f"<a:p><a:r><a:t>{title}</a:t></a:r></a:p>"
                f"<a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:spTree></p:cSld></p:sld>",
            )
    return out.getvalue()


def md_bytes() -> bytes:
    key = "sk-abcdefghijklmnopqrstuv1234"
    return f"# Summary\n{DOC_TEXT}\n\n# Empty\n\n# Notes\nKey {key} here.\n".encode()


def html_bytes() -> bytes:
    return (
        f"<html><head><title>Report</title><script>var x=1;</script></head><body>"
        f"<h1>Findings</h1><p>{DOC_TEXT}</p><h2>Gaps</h2><h2>Contacts</h2>"
        f"<p>Call +1 415-555-0100 now.</p></body></html>"
    ).encode()


def png_bytes() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (16, 8), (200, 10, 10)).save(out, format="PNG")
    return out.getvalue()


def wav_bytes() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * 8000)
    return out.getvalue()


def zip_bytes(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return out.getvalue()


def tar_gz_bytes(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return out.getvalue()


def gz_bytes(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def zip_bomb() -> bytes:
    """One member of 200 MB of zeros (compresses about 1000 to 1)."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z,
        z.open("bomb.txt", "w", force_zip64=True) as member,
    ):
        block = b"0" * (1 << 20)
        for _ in range(200):
            member.write(block)
    return out.getvalue()


def traversal_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("../../etc/evil.csv", csv_bytes())
        z.writestr("/abs/evil.csv", csv_bytes())
        z.writestr("ok/data.csv", csv_bytes())
    return out.getvalue()


VALID: dict[str, tuple[str, bytes]] = {}


def valid() -> dict[str, tuple[str, bytes]]:
    """``kind -> (file name, bytes)`` for every supported type."""
    if not VALID:
        VALID.update(
            {
                "csv": ("data.csv", csv_bytes()),
                "tsv": ("data.tsv", csv_bytes(sep="\t")),
                "xlsx": ("book.xlsx", xlsx_bytes()),
                "parquet": ("data.parquet", parquet_bytes()),
                "json": ("data.json", json_bytes()),
                "jsonl": ("data.jsonl", jsonl_bytes()),
                "pdf": ("report.pdf", pdf_bytes()),
                "docx": ("report.docx", docx_bytes()),
                "pptx": ("deck.pptx", pptx_bytes()),
                "txt": ("notes.txt", DOC_TEXT.encode()),
                "md": ("notes.md", md_bytes()),
                "html": ("page.html", html_bytes()),
                "png": ("pic.png", png_bytes()),
                "wav": ("tone.wav", wav_bytes()),
                "zip": ("bundle.zip", zip_bytes({"a.csv": csv_bytes(), "b.md": md_bytes()})),
                "gz": ("bundle.tar.gz", tar_gz_bytes({"a.csv": csv_bytes()})),
            }
        )
    return VALID


def corrupt(kind: str, data: bytes) -> bytes:
    """A broken copy: truncated in the middle (text types: an unclosed quote or bracket)."""
    if kind in ("csv", "tsv"):
        return data + b'4,"unterminated value\n'
    if kind == "json":
        return data[: len(data) // 2]
    if kind == "jsonl":
        return data + b'{"id": \n'
    if kind in ("txt", "md"):
        return data + b"\xff\xfe\xfa bad bytes\n"
    if kind == "html":
        return data[: len(data) // 2]
    return data[: max(8, len(data) // 2)]
