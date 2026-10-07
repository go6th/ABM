"""Phase 4 gate checks: no model in the new packages, and the skill's client-output scanner over a
rendered sample of every tenant-visible message kind (alert, sensitive alert, digest, Summary diff,
CRM payload, status page, Open Door event)."""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from alerts import digest, sensitive, templates
from crm_writeback import payloads
from opendoor import events
from refresh import delta
from refresh.summary_diff import render
from tests.phase4.conftest import NOW

ROOT = Path(__file__).resolve().parents[2]
NEW = ("refresh", "signals", "alerts", "governance", "crm_writeback", "opendoor")
LLM = (
    "anthropic",
    "openai",
    "llm",
    "langchain",
    "vertexai",
    "google.generativeai",
    "account_intelligence.agents",
    "litellm",
)


def test_no_model_import_in_the_phase4_packages() -> None:
    found = []
    for pkg in NEW:
        for p in (ROOT / "src" / pkg).rglob("*.py"):
            for node in ast.walk(ast.parse(p.read_text())):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                found += [
                    f"{p.name}:{m}"
                    for m in mods
                    if any(m == x or m.startswith(x + ".") for x in LLM)
                ]
    assert found == []


def test_every_rendered_message_passes_the_client_output_scanner(tmp_path: Path) -> None:
    alert = templates.render(
        "new_build_role",
        account="acme.example",
        summary="Hiring a platform owner.",
        grade="confirmed",
        source_code="S-0004",
        event_type="expansion",
        is_lead=False,
        app_link="https://app.test/alerts/x",
    )
    sens = templates.render(
        "tier_one_news",
        account="acme.example",
        summary="ignored",
        grade="confirmed",
        source_code="S-0005",
        event_type="layoff",
        is_lead=False,
        app_link="https://app.test/alerts/y",
    )
    lines, more = digest.assemble(
        [digest.Item("fp", 5, NOW, f"{alert.subject}: {alert.body}")], limit=5
    )
    d = delta.compute(
        [],
        [delta.FactView("f", "x", "news_event", "probable", "S-0001")],
        [],
        {},
        {},
        checked=["company_news"],
        checked_at=NOW,
    )
    diff = render(d, since_version=1, since_date=date(2026, 3, 1))
    crm = payloads.render(
        "note",
        account_record_id="001A",
        account_label="acme.example",
        items=[payloads.Item("Hiring a platform owner", "S-0004", "public", "expansion", True)],
        marker_prefix="m:",
        title_chars=120,
        body_chars=4000,
    )
    spec = importlib.util.spec_from_file_location("status_gen", ROOT / "ops/status/generate.py")
    assert spec and spec.loader
    status = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(status)
    page = status.render(
        {
            "generated_at": "2026-03-02T09:00Z",
            "components": [{"name": "Alerts", "status": "operational"}],
        }
    )
    event = events.schema("signal_event.v1.json")["examples"][0]
    samples = {
        "alert.txt": f"{alert.subject}\n{alert.body}",
        "sensitive.txt": f"{sens.subject}\n{sens.body}",
        "digest.txt": "\n".join(lines),
        "summary_diff.txt": diff,
        "crm.txt": f"{crm.title}\n{crm.body}",
        "status.html": page,
        "opendoor.json": json.dumps(event),
    }
    for name, text in samples.items():
        (tmp_path / name).write_text(text)
    assert sensitive.LABEL in samples["sensitive.txt"] and more == 0
    out = subprocess.run(
        [
            sys.executable,
            str(
                ROOT / ".claude/skills/account-intelligence-platform/scripts/scan_client_output.py"
            ),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
