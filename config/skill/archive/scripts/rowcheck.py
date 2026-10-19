#!/usr/bin/env python3
"""Confirm every table row in a .docx appears in its rendered PDF.

Why: a table that crosses a page break can silently lose rows in rendering.
On the first Alcon build the People table showed 1 of 12 rows and nobody looked.

Usage:
    python rowcheck.py Account_Summary Sources_and_Evidence
Pass file stems. Each needs stem.docx and stem.pdf side by side. Make the PDF with
    python /mnt/skills/public/docx/scripts/office/soffice.py --headless --convert-to pdf stem.docx
Exit code is 1 if any row is missing.
"""
import re
import subprocess
import sys
import zipfile


def check(stem):
    xml = zipfile.ZipFile(stem + ".docx").read("word/document.xml").decode()
    pdf = subprocess.run(["pdftotext", "-layout", stem + ".pdf", "-"], capture_output=True, text=True).stdout
    flat = re.sub(r"\s+", " ", pdf)
    missing = total = 0
    for ti, tbl in enumerate(re.findall(r"<w:tbl>.*?</w:tbl>", xml, flags=re.S)):
        for row in re.findall(r"<w:tr[ >].*?</w:tr>", tbl, flags=re.S):
            cells = re.findall(r"<w:tc>.*?</w:tc>", row, flags=re.S)
            if not cells:
                continue
            text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", cells[0])).replace("&amp;", "&")
            words = text.split()[:3]
            if not words:
                continue
            total += 1
            if " ".join(words) not in flat and words[0] not in flat:
                missing += 1
                print(f"  MISSING table {ti}: {text[:60]}")
    print(f"{stem}: rows {total}, missing {missing}")
    return missing


if __name__ == "__main__":
    bad = sum(check(s) for s in sys.argv[1:])
    sys.exit(1 if bad else 0)
