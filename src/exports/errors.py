"""Typed error hierarchy for the export builders (spec 4).

Every failure is typed and carries a stable ``code`` plus ``items``: the row ids, source codes or
check names involved, never row text (a message can reach a log; content must not). The builder
collects every error of a build and ends the run PARTIAL ``build_failed``; ``NotConfigured`` ends it
PARTIAL ``builders_not_configured``. Neither is ever reported as success.
"""

from __future__ import annotations

from collections.abc import Iterable

from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured

# Items listed in an error's text (all of them stay on ``items``).
MAX_LISTED = 25


class ExportError(RuntimeError):
    """Base of every builder error."""

    code = "export_error"

    def __init__(self, message: str, items: Iterable[str] = ()) -> None:
        self.items: tuple[str, ...] = tuple(items)
        self.message = message
        suffix = ""
        if self.items:
            shown = ", ".join(self.items[:MAX_LISTED])
            more = (
                f" and {len(self.items) - MAX_LISTED} more" if len(self.items) > MAX_LISTED else ""
            )
            suffix = f" ({len(self.items)} item(s): {shown}{more})"
        super().__init__(f"{self.code}: {message}{suffix}")


class MissingContent(ExportError):
    """A required content row is absent (report sections, run inputs). The file is refused."""

    code = "missing_content"


class MissingProvenance(ExportError):
    """A row has no URL or no registered source code (B4). Items are the row ids."""

    code = "missing_provenance"


class BannedContent(ExportError):
    """Authored prose or a row breaks a content rule (dash, stale phrase, statement policy,
    partner framing, unregistered number)."""

    code = "banned_content"


class OrphanCitation(ExportError):
    """A cited source code does not exist in the Evidence file, or a graded row cites none (B5)."""

    code = "orphan_citation"


class RowCapExceeded(ExportError):
    """More rows than the cap: the build fails rather than truncating silently (B3)."""

    code = "row_cap_exceeded"


class RowConservation(ExportError):
    """Rows read, rows rendered and the snapshot count disagree (B3)."""

    code = "row_conservation"


class LeakError(ExportError):
    """A forbidden term or a non-public row reached a client-facing file (B8, B9). Items are the
    patterns that fired and where, never the matched text."""

    code = "leak"


class RenderError(ExportError):
    """The renderer failed (timeout, overflow, non-zero exit, malformed output)."""

    code = "render_failed"


class SchemaViolation(ExportError):
    """A row or render model violates its schema or the grading vocabulary (B7)."""

    code = "schema_violation"


class UnsafePackage(ExportError):
    """A zip member name, size or count breaks the package rules (B13)."""

    code = "unsafe_package"


class ConfigError(ExportError):
    """Export configuration is invalid or missing an entry the build needs."""

    code = "config_error"


class NotConfigured(ExportError, PlatformNotConfigured):
    """A production adapter is missing. The run ends PARTIAL ``builders_not_configured``."""

    code = "builders_not_configured"
