"""Vulture whitelist: names that look unused but are part of a typed interface.

Parameters of ``Protocol`` method stubs (``connectors.base.Connector.parse``, the workflow spec
ports) are part of each port's contract; implementations use them.
"""

provenance_base  # noqa: B018, F821 - Connector.parse parameter (Protocol stub)
arguments  # noqa: B018, F821 - ConnectorPort.call_operation parameter
detections  # noqa: B018, F821 - DetectionRepo.upsert_many parameter
actual_cost_usd  # noqa: B018, F821 - WalletPort.settle_once parameter
credential  # noqa: B018, F821 - IdentityVerifier.verify protocol parameter
artifact_id  # noqa: B018, F821 - LinkSigner.create_link protocol parameter
principal_id  # noqa: B018, F821 - RateLimiterPort.consume protocol parameter
total_micros  # noqa: B018, F821 - CreditPort request contract; adapter rebuilds unit estimates
folder_id  # noqa: B018, F821 - ConnectorClient.list_folder contract and unconfigured adapter
timeout_millis  # noqa: B018, F821 - OpenTelemetry SpanExporter.force_flush contract
parent_context  # noqa: B018, F821 - OpenTelemetry SpanProcessor.on_start contract
functions  # noqa: B018, F821 - PeopleSearchPort contract; provider args are technology-scoped
row_keys  # noqa: B018, F821 - TrackerPort.mark contract
to  # noqa: B018, F821 - Mailer.send contract and unconfigured adapter

# pydantic-settings ``settings_customise_sources`` signature (config._Explicit):
# the framework passes every source; only ``init_settings`` is used.
settings_cls  # noqa: B018, F821 - pydantic-settings sources
env_settings  # noqa: B018, F821 - pydantic-settings sources
dotenv_settings  # noqa: B018, F821 - pydantic-settings sources
file_secret_settings  # noqa: B018, F821 - pydantic-settings sources
