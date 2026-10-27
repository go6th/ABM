"""The gate report: findings per check, warnings, one verdict. JSON-serialisable for the UI.

A ``Finding`` names the check, the file and what was found in plain words; it carries counts,
kinds and the offending number, never a person's name, a client's name or document text.
A check is PASS, FAIL or SKIP (a tool the check needs is not installed, with the reason).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

PASS = "PASS"  # nosec B105 - a verdict word, not a secret
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class Finding:
    """One thing a check found."""

    check: str
    message: str
    file: str | None = None
    severity: str = "fail"


@dataclass
class CheckResult:
    """The outcome of one check."""

    name: str
    status: str
    findings: list[Finding] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> list[Finding]:
        """The findings that fail the check."""
        return [f for f in self.findings if f.severity == "fail"]

    @property
    def warnings(self) -> list[Finding]:
        """The findings that only warn."""
        return [f for f in self.findings if f.severity == "warn"]


def result(
    name: str, findings: list[Finding], details: dict[str, Any] | None = None
) -> CheckResult:
    """A PASS or FAIL result from a check's findings."""
    status = FAIL if any(f.severity == "fail" for f in findings) else PASS
    return CheckResult(name, status, findings, details or {})


def skipped(name: str, reason: str) -> CheckResult:
    """A SKIP result with its reason."""
    return CheckResult(name, SKIP, [], {"reason": reason})


@dataclass
class GateReport:
    """Every check's result, the data warnings and the manifest written."""

    checks: list[CheckResult] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    manifest: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        """True when no check failed (a skipped check does not fail the gate)."""
        return all(c.status != FAIL for c in self.checks)

    def check(self, name: str) -> CheckResult | None:
        """The result of one check by name."""
        return next((c for c in self.checks if c.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        """The report as plain data."""
        return {
            "passed": self.passed,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "findings": [asdict(f) for f in c.findings],
                    "details": c.details,
                }
                for c in self.checks
            ],
            "warnings": [asdict(w) for w in self.warnings],
            "manifest": self.manifest,
        }

    def to_json(self) -> str:
        """The report as JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str)

    def text(self) -> str:
        """The report as the command line prints it."""
        lines = []
        for c in self.checks:
            lines.append(f"{c.status:4} {c.name}")
            if c.status == SKIP:
                lines.append(f"       skipped: {c.details.get('reason', '')}")
            for f in c.findings:
                where = f" [{f.file}]" if f.file else ""
                lines.append(f"       {f.severity}: {f.message}{where}")
        if self.warnings:
            lines.append("warnings")
            for w in self.warnings:
                where = f" [{w.file}]" if w.file else ""
                lines.append(f"       {w.message}{where}")
        lines.append("GATE " + ("PASS" if self.passed else "FAIL"))
        return "\n".join(lines)
