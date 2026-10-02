"""Provider code to vendor name. INTERNAL: imported by internal tooling only.

The rest of the connector layer, every storage key, log line, metric label, error
message and client-safe view uses provider codes. The display names come from the
``display_name`` fields of config/providers.yaml, which is the single place they live
in configuration; this module is the single place code may read them.
"""

from __future__ import annotations

from account_intelligence.connectors.base import ProviderCode
from account_intelligence.connectors.settings import ProvidersConfig


def vendor_names(config: ProvidersConfig) -> dict[ProviderCode, str]:
    return {code: cfg.display_name for code, cfg in config.providers.items()}
