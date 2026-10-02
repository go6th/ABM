"""Compatibility exports for tenant-scoped data-layer repositories.

New code should import from ``src.db.repositories`` by domain.
"""

from src.db.errors import DataLayerError
from src.db.repositories.checklist import DEFAULT_CHECKLIST, load_checklist, validate_coverage_proof
from src.db.repositories.contracts import (
    ENGINE_GRADES,
    PRODUCT_GRADES,
    SOURCE_CODE_FORMAT,
    ChecklistRow,
    CoverageStatus,
    FactEvidence,
)
from src.db.repositories.coverage import record_coverage, seed_coverage_rows
from src.db.repositories.facts import create_fact_with_evidence
from src.db.repositories.job_postings import upsert_job_posting
from src.db.repositories.normalization import (
    fingerprint_value,
    normalize_source_url,
    source_url_hash,
)
from src.db.repositories.people import get_or_create_person, merge_person
from src.db.repositories.sources import get_or_create_source
from src.db.repositories.tech_detections import upsert_tech_detection

__all__ = [
    "DEFAULT_CHECKLIST",
    "ENGINE_GRADES",
    "PRODUCT_GRADES",
    "SOURCE_CODE_FORMAT",
    "ChecklistRow",
    "CoverageStatus",
    "DataLayerError",
    "FactEvidence",
    "create_fact_with_evidence",
    "fingerprint_value",
    "get_or_create_person",
    "get_or_create_source",
    "load_checklist",
    "merge_person",
    "normalize_source_url",
    "record_coverage",
    "seed_coverage_rows",
    "source_url_hash",
    "upsert_job_posting",
    "upsert_tech_detection",
    "validate_coverage_proof",
]
