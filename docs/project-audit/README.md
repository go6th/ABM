# Project audit (3 Nov 2026)

* `ABM_Generator_Project_Audit.pdf` is the handover guide (about 115 pages).
* `reports/` holds the five read-only audit reports the appendices are built from.
* `src/` builds the PDF: `build.py` (needs the `markdown`, `pypdf` and `pymupdf` packages and a Chromium on the path),
  `svgkit.py` and `figs_*.py` draw the figures, `content_*.py` hold the chapter text.

Rebuild: `python src/build.py`
