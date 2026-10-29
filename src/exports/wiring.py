"""Wiring of the export builders (the ``BuilderPort`` adapter) for the app container.

``EXPORT_RENDERER`` chooses the Word renderer:

* ``python-interim`` (the default everywhere since 2026-10-14, FIX_LOG D10): the neutral
  python-docx renderer behind the same contract and sandbox. It works today, so cloud runs build
  their Word files instead of ending PARTIAL ``builders_not_configured``.
* ``node``: the ported Node docx library (D-26). It is ``NotConfigured`` until the legacy library
  is supplied and ported; choosing it before then gives the builder no renderer and every build
  ends ``builders_not_configured``. Switch back by setting ``EXPORT_RENDERER=node`` once ported.

``EXPORT_RENDER_CHECKS=on`` adds the LibreOffice PDF row-presence and recalculation checks to every
build (the converter must be installed in the image; the full gate stays behind ``GatePort``).
"""

from __future__ import annotations

from collections.abc import Mapping

from account_intelligence.db.session import tenant_txn
from account_intelligence.grading.config import load_grading_config
from account_intelligence.platform.ports import Clock, PayloadStore
from exports.builder import ExportBuilder
from exports.config import default_export_config
from exports.errors import ConfigError, NotConfigured
from exports.readers.pg import PgContentReader
from exports.store import PgDeliverableStore
from exports.verify import LibreOffice
from exports.word.renderer import (
    RendererCommand,
    SubprocessWordRenderer,
    node_renderer,
    python_renderer,
)

RENDERERS = ("node", "python-interim")


def renderer_command(choice: str) -> RendererCommand | None:
    """The renderer command for ``choice``; None when it is not configured (Node not ported)."""
    if choice not in RENDERERS:
        raise ConfigError(f"EXPORT_RENDERER must be one of {RENDERERS}")
    if choice == "python-interim":
        return python_renderer()
    try:
        return node_renderer()
    except NotConfigured:
        return None


def build_export_builder(
    payloads: PayloadStore, clock: Clock, env: Mapping[str, str], default_renderer: str
) -> ExportBuilder:
    """The production ``BuilderPort`` over Postgres, the payload store and the chosen renderer."""
    cfg = default_export_config()
    command = renderer_command(env.get("EXPORT_RENDERER", default_renderer))
    checks = LibreOffice() if env.get("EXPORT_RENDER_CHECKS") == "on" else None
    # archived-copy links in the files are absolute addresses on the public web origin
    base = (env.get("PUBLIC_BASE_URL") or "").strip().rstrip("/") or None
    return ExportBuilder(
        PgContentReader(payloads, batch=cfg.rules.limits.keyset_batch, archive_base=base),
        PgDeliverableStore(tenant_txn, payloads, clock, cfg.rules_hash),
        SubprocessWordRenderer(command, cfg.rules.renderer) if command else None,
        cfg,
        load_grading_config(),
        pdf=checks,
        recalc=checks,
    )
