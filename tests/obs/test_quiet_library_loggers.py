"""Library chatter whose message the allow-list drops is kept at WARNING and above, so a run no
longer writes thousands of anonymous ``log`` lines (httpx once per HTTP call, uvicorn's access
log once per request); a problem those libraries report is still logged."""

from __future__ import annotations

import logging

from obs.logging import configure_logging


def test_httpx_and_the_access_log_are_quiet_below_warning() -> None:
    before = {n: logging.getLogger(n).level for n in ("httpx", "httpcore", "uvicorn.access")}
    try:
        configure_logging("INFO", "test")
        for name in ("httpx", "httpcore", "uvicorn.access"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING, name
        assert logging.getLogger("uvicorn.error").getEffectiveLevel() == logging.INFO
    finally:
        for name, level in before.items():
            logging.getLogger(name).setLevel(level)
