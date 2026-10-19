"""The verbatim companion appendix is the sole personal-name scan exemption."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from agents.naming import digest_hits
from scripts import abm_name_scan

COMPANION = abm_name_scan.ROOT / "config/skill/archive/references/session-environment.md"


def guarded_line_and_word() -> tuple[str, str]:
    """Read a guarded name from the companion without spelling it in this test source."""
    for line in COMPANION.read_text(encoding="utf-8").splitlines():
        offsets = digest_hits(line)
        if offsets:
            word = re.match(r"[^\W\d_]+", line[offsets[0] :], re.UNICODE)
            if word:
                return line, word.group()
    raise AssertionError("companion appendix no longer contains the guarded name used by this test")


def test_name_scan_exemption_is_exact_to_companion_file() -> None:
    line, _ = guarded_line_and_word()
    relative = Path("tests") / f"name-scan-probe-{uuid4().hex}.txt"
    probe = abm_name_scan.ROOT / relative
    try:
        probe.write_text(line, encoding="utf-8")
        files = set(abm_name_scan.files())
        assert abm_name_scan.ROOT / "config/skill/archive/SKILL.md" in files
        assert abm_name_scan.ROOT / "config/skill/archive/references/checklist.md" in files
        assert COMPANION not in files
        assert probe in files
        hits = abm_name_scan.scan()
        assert (relative.as_posix(), 1) in hits
        acknowledged, errors = abm_name_scan.acknowledged_archive_hits()
        assert not errors
        assert {path for path, _ in hits if path != relative.as_posix()} <= acknowledged
        assert abm_name_scan.unexpected_hits(hits, acknowledged) == [(relative.as_posix(), 1)]
        assert not any(
            path == "config/skill/archive/references/session-environment.md" for path, _ in hits
        )
    finally:
        probe.unlink(missing_ok=True)


def test_same_guarded_name_is_rejected_in_client_bound_model_output() -> None:
    import asyncio
    from datetime import UTC, datetime
    from uuid import uuid4

    import pytest

    from agents.contracts import BudgetMeter, load_registry
    from agents.errors import OutputRejected
    from agents.ports import RunContext
    from agents.runtime import call_agent
    from agents.schemas import Claim, DistillInput, DistillOutput

    class Model:
        def __init__(self, output: object) -> None:
            self.output = output

        async def call(self, *args: object) -> tuple[object, int]:
            return self.output, 1

    _, name = guarded_line_and_word()
    inp = DistillInput(account_name="A", audience="client", facts=())
    output = DistillOutput(narrative=(Claim(text=name, fact_ids=("f1",)),))
    ctx = RunContext(uuid4(), uuid4(), "a.example", "A", "a.example", False, "v")
    contract = load_registry()["distill"]
    with pytest.raises(OutputRejected, match="client_output_leak"):
        asyncio.run(
            call_agent(
                contract,
                Model(output),
                BudgetMeter(contract, datetime(2026, 10, 8, tzinfo=UTC)),
                ctx,
                inp,
                client_bound=True,
            )
        )
