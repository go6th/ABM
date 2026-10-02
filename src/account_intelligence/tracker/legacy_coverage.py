"""Legacy coverage implementation moved from workflow."""

from __future__ import annotations
from dataclasses import dataclass, field
from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.enums import CoverageStatus

@dataclass(frozen=True)
class CoverageProof:
    queries: tuple[str, ...] = ()
    tool_category: str | None = None
    result_count: int | None = None
    reason: str | None = None
    source_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.result_count is not None and self.result_count < 0:
            raise ValueError("result_count cannot be negative")
        if not any((self.queries, self.tool_category, self.reason, self.source_refs)):
            raise ValueError("coverage status requires proof")
        if self.queries and not self.tool_category:
            raise ValueError("queries require a tool category")
        if self.tool_category and self.result_count is None:
            raise ValueError("tool proof requires a result count")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason cannot be blank")

@dataclass
class CoverageLedger:
    priorities: dict[str, str]
    statuses: dict[str, CoverageStatus] = field(default_factory=dict)
    proofs: dict[str, CoverageProof] = field(default_factory=dict)

    @classmethod
    def from_config(cls) -> CoverageLedger:
        import yaml
        data = yaml.safe_load((CONFIG_DIR / "checklist.yaml").read_text())
        priorities = {
            str(row.get("key", row.get("id"))): str(row["priority"]).lower() for row in data["rows"]
        }
        return cls(priorities)

    def set_status(self, key: str, status: CoverageStatus | str, proof: CoverageProof) -> None:
        if key not in self.priorities:
            raise KeyError(f"unknown coverage row: {key}")
        proof.validate()
        resolved = CoverageStatus(status)
        if resolved is CoverageStatus.BLOCKED and not proof.reason:
            raise ValueError("blocked coverage requires a reason")
        self.statuses[key] = resolved
        self.proofs[key] = proof

    def high_rows_resolved(self) -> bool:
        return all(
            key in self.statuses and key in self.proofs
            for key, priority in self.priorities.items()
            if priority == "high"
        )
