"""The parity quality gate: every render passes it or nothing is delivered.

Checks (one module each, every one returning a ``report.CheckResult``):

* ``scan``: data tool names, provider hosts and guarded person names in every Word and Excel
  member (a pass is zero hits; the word "credit" in posting text is a warning);
* ``doc_checks``: orphan citations, banned dashes, stale phrases and excluded names on the PDF
  text of each Word file;
* ``rowcheck``: every table row of each Word file renders in its PDF;
* ``numbers``: every number in prose equals a computed value;
* ``workbook``: recalculated workbooks have no error values, every formula is still a formula
  with a cached value, and the Summary values equal ``parity.counts``;
* ``confidentiality`` with ``scrub``: the zip allow-list, refused file names, the pursuing
  client's real name, its aliases and email addresses;
* ``warnings``: data warnings that never fail the gate.

``zipper`` builds the workspace zip, ``manifest`` the file manifest, ``report`` the
JSON-serialisable report and ``__main__`` runs everything from the command line.
"""
