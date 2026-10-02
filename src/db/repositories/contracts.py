from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

CoverageStatus = Literal["Done", "Partial", "Not done", "Blocked", "None"]
SOURCE_CODE_FORMAT = "S-{value:04d}"
PRODUCT_GRADES = {"confirmed", "probable", "hypothesis", "lead"}
ENGINE_GRADES = {"A", "B", "C", "D", "CONFLICTING", "BLOCKED"}


@dataclass(frozen=True)
class ChecklistRow:
    row_id: str
    pass_number: int
    label: str
    priority: str


@dataclass(frozen=True)
class FactEvidence:
    source_id: UUID
    source_code: str
    lineage_group: str
    quote_text: str
    retrieved_at: datetime
