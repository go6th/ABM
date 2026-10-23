"""The Mail and drive setup script's tfvars edits (scripts/maildrive_setup.py), offline: the domain
and the maildrive block are replaced in place (or added), and nothing else in the file changes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

TFVARS = """project_id        = "abmgenerator"
domain            = "136-81-60-29.sslip.io" # temporary
github_repository = "go6th/ABM"
# maildrive = { google_client_id = "", microsoft_client_id = "" } # an id here needs its secret
staff = {
  workspace_domain = "6th.ai"
}
"""


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "maildrive_setup", ROOT / "scripts" / "maildrive_setup.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_domain_is_replaced_in_place() -> None:
    out = _module().set_domain(TFVARS, "app.6th.ai")
    assert 'domain            = "app.6th.ai"' in out
    assert "sslip.io" not in out
    assert sum(line.startswith("domain ") for line in out.splitlines()) == 1
    assert 'workspace_domain = "6th.ai"' in out


def test_the_maildrive_block_is_added_then_replaced() -> None:
    m = _module()
    google = "123-abc.apps.googleusercontent.com"
    once = m.set_maildrive(TFVARS, google, "")
    assert f'maildrive = {{ google_client_id = "{google}", microsoft_client_id = "" }}' in once
    assert "# maildrive = {" in once  # the commented example is left alone
    ms = "00000000-aaaa-bbbb-cccc-000000000000"
    twice = m.set_maildrive(once, google, ms)
    assert twice.count("\nmaildrive = {") == 1
    assert f'microsoft_client_id = "{ms}"' in twice
    assert "staff = {" in twice and 'project_id        = "abmgenerator"' in twice


def test_inputs_are_validated() -> None:
    m = _module()
    assert m.DOMAIN_RE.match("app.6th.ai")
    assert not m.DOMAIN_RE.match("https://app.6th.ai")
    assert m.GOOGLE_ID_RE.match("123456-abcdef.apps.googleusercontent.com")
    assert not m.GOOGLE_ID_RE.match("not-an-id")
    assert m.MS_ID_RE.match("0a1b2c3d-0000-4444-8888-abcdefabcdef")
