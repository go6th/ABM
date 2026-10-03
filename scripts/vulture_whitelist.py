"""Vulture whitelist: names that look unused but are part of a typed interface.

Parameters of ``Protocol`` method stubs (``connectors.base.Connector.parse``, the workflow spec
ports) are part of each port's contract; implementations use them.
"""

provenance_base  # noqa: B018, F821 - Connector.parse parameter (Protocol stub)
arguments  # noqa: B018, F821 - ConnectorPort.call_operation parameter
detections  # noqa: B018, F821 - DetectionRepo.upsert_many parameter
actual_cost_usd  # noqa: B018, F821 - WalletPort.settle_once parameter
