"""Coverage row state machine; a status cannot be recorded without its proof."""

from account_intelligence.tracker.legacy_coverage import CoverageLedger, CoverageProof

__all__ = ["CoverageProof", "CoverageLedger"]
