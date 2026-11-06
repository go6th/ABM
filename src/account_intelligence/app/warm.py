"""Warm the configuration caches at startup, off the event loop (ADR-0130).

Every loader below is cached for the life of the process (``lru_cache`` or a file-state cache),
but its FIRST call parses YAML, validates it and compiles patterns: tens to hundreds of
milliseconds of pure Python. Made lazily, that first call lands inside the first research's
graph node or the first request, on the event loop (the event-loop audit,
``docs/audits/event-loop.md``). ``warm_caches`` makes every first call once, in a worker thread,
before the service takes traffic. A loader that fails here is logged and left to fail where it is
used (startup never stops on a warm-up).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from obs.logging import logger

_log = logger(__name__)


def _loaders() -> list[tuple[str, Callable[[], Any]]]:
    from account_intelligence.connectors.cache import load_research_cache_config
    from account_intelligence.connectors.settings import load_providers_config
    from account_intelligence.platform.llm import load_batch_config
    from account_intelligence.safety.client_output import load_patterns
    from agents.config import skill_config
    from agents.naming import name_digests, provider_terms
    from exports.config import default_export_config, provider_codes
    from exports.store import _missing
    from jobs.config import export_rule_texts, load_jobs_config
    from llm.choice import catalogue, task_efforts
    from llm.cost import prices
    from llm.data_class import load_policy
    from parallel.config import load_parallel_config

    def research_modules() -> None:
        # the research graph's modules (their first import parses config at module level)
        import agents.production.wiring  # noqa: F401
        import agents.runtime  # noqa: F401

    return [
        ("providers", load_providers_config),
        ("research_cache", load_research_cache_config),
        ("parallel", load_parallel_config),
        ("client_output_patterns", load_patterns),
        ("skill", skill_config),
        ("name_digests", name_digests),
        ("provider_terms", provider_terms),
        ("export", default_export_config),
        ("provider_codes", provider_codes),
        ("model_catalogue", catalogue),
        ("task_efforts", task_efforts),
        ("data_class_policy", load_policy),
        ("prices", prices),
        ("batch", load_batch_config),
        ("jobs", load_jobs_config),
        ("export_rule_texts", export_rule_texts),
        ("storage_errors", _missing),
        ("research_modules", research_modules),
    ]


def warm_caches() -> list[str]:
    """Make the first call of every cached loader; the names that failed (logged)."""
    failed: list[str] = []
    for name, load in _loaders():
        try:
            load()
        except Exception as error:  # noqa: BLE001 - a warm-up never stops startup
            failed.append(name)
            _log.warning(
                "startup.warm_failed",
                extra={"error_code": name, "error_class": type(error).__name__},
            )
    return failed
