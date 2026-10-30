"""The third-party scan: no data tool name, provider host or guarded person name in any Word or
Excel file (the port of the skill's ``third_party_scan.py``, gate 9).

Every XML and relationship member of each package is scanned, so hyperlink targets and document
properties count, not only the visible text. The word lists are the repository's
(``agents.naming``: the configured providers' vendor names, their registrable hosts and the
guarded name digests), not a copy. Zero hits is a pass. The word "credit" is counted as a
warning only: posting text legitimately says credit notes or carbon credits, and a reviewer
reads those. Findings carry the kind of hit and a count, never the matched text.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from parity.gate.files import office_xml_members
from parity.gate.report import CheckResult, Finding, result

NAME = "third_party_scan"
_CREDIT = re.compile(r"\bcredits?\b", re.I)


def scan_file(path: Path) -> tuple[Counter[str], int]:
    """Hits by kind (``tool_name``, ``provider_host``, ``person_name``) and the credit count."""
    from agents.naming import digest_hits, third_party_domain_hits, tool_name_hits

    hits: Counter[str] = Counter()
    credits = 0
    for _member, text in office_xml_members(path.read_bytes()):
        hits["tool_name"] += len(tool_name_hits(text))
        hits["provider_host"] += len(third_party_domain_hits(text))
        hits["person_name"] += len(digest_hits(text))
        credits += len(_CREDIT.findall(text))
    return hits, credits


def run_scan(files: list[Path]) -> CheckResult:
    """The scan over every file: a finding per kind with hits, a warning per file naming
    credits."""
    findings: list[Finding] = []
    details: dict[str, dict[str, int]] = {}
    for path in files:
        hits, credits = scan_file(path)
        details[path.name] = {**{k: v for k, v in hits.items() if v}, "credit_words": credits}
        for kind, n in sorted(hits.items()):
            if n:
                findings.append(Finding(NAME, f"{kind}: {n} hit(s)", path.name))
        if credits:
            findings.append(
                Finding(
                    NAME,
                    f"the word credit appears {credits} time(s); read them, block only if they"
                    " are about tool credits",
                    path.name,
                    "warn",
                )
            )
    return result(NAME, findings, {"files": details})
