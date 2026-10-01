#!/usr/bin/env python3
"""Scan client-facing files for forbidden content (PRD 3.4 third-party scan, SaaS 7.5 leakage check).

Usage: python scripts/scan_client_output.py [--extra extra_terms.txt] PATH [PATH ...]
PATH can be a file or directory. .docx/.xlsx/.pptx/.zip are opened and every XML/text member is
scanned (document XML, relationship files, workbook cells, properties). Patterns come from
assets/client_output_denylist.txt (regex, case-insensitive). --extra adds literal terms, one per line
(tenant names, emails, internal source IDs from the redaction map). Exit 1 on any hit.
Not covered: text inside images, PDFs (convert or extract text first), and semantic leaks (use the
embedding-similarity leak detector for those).
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "assets" / "client_output_denylist.txt"
ZIPS = {".docx", ".xlsx", ".pptx", ".zip"}
TEXT_MEMBERS = {".xml", ".rels", ".txt", ".md", ".json", ".csv", ".yaml", ".yml", ".html", ".sql", ".py", ".js"}


def load_patterns(path: Path, extra: Path | None) -> list[re.Pattern]:
    pats = [re.compile(l.strip(), re.I) for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]
    if extra:
        pats += [re.compile(re.escape(l.strip()), re.I) for l in extra.read_text().splitlines() if l.strip() and not l.startswith("#")]
    return pats


def iter_texts(path: Path):
    if path.suffix.lower() in ZIPS:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                data = z.read(n)
                if Path(n).suffix.lower() in ZIPS:
                    import io
                    with zipfile.ZipFile(io.BytesIO(data)) as inner:
                        for m in inner.namelist():
                            if Path(m).suffix.lower() in TEXT_MEMBERS:
                                yield f"{path}!{n}!{m}", inner.read(m).decode("utf-8", "ignore")
                elif Path(n).suffix.lower() in TEXT_MEMBERS:
                    yield f"{path}!{n}", data.decode("utf-8", "ignore")
                # object keys and member names are scanned too
                yield f"{path}!{n} (member name)", n
    elif path.suffix.lower() in TEXT_MEMBERS or path.suffix == "":
        yield str(path), path.read_text(errors="ignore")
    else:
        print(f"SKIP {path} (unsupported type; extract text first)", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--extra")
    ap.add_argument("--denylist", default=str(DEFAULT))
    a = ap.parse_args()
    pats = load_patterns(Path(a.denylist), Path(a.extra) if a.extra else None)
    files = []
    for p in a.paths:
        pp = Path(p)
        files += [f for f in sorted(pp.rglob("*")) if f.is_file()] if pp.is_dir() else [pp]
    hits = 0
    for f in files:
        for where, text in iter_texts(f):
            for pat in pats:
                for m in pat.finditer(text):
                    hits += 1
                    s = max(0, m.start() - 20)
                    print(f"HIT {where}: /{pat.pattern}/ ...{text[s:m.end() + 20]!r}")
    print(f"{'FAIL' if hits else 'OK'}: {len(files)} file(s) scanned, {hits} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
