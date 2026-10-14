"""The typed API contract (D-91 step 0): the committed OpenAPI snapshot matches the code, and every
JSON operation declares a typed response, so the generated web client has no untyped reply.

GUARANTEE(test_every_json_operation_has_a_typed_response)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import export_openapi  # noqa: E402

# Operations whose success body is not JSON (a file, a CSV, a redirect or an event stream).
NOT_JSON = {
    ("get", "/v1/audit/export"),
    ("get", "/v1/usage/export"),
    ("get", "/v1/exports/{package_id}/files/{index}"),
    ("get", "/v1/runs/{run_id}/internal/file"),  # the Internal Only Word file
    ("get", "/v1/runs/{run_id}/events"),
    ("get", "/v1/connectors/oauth/callback"),
    ("get", "/v1/sso/{slug}/start"),
    ("get", "/v1/sso/oidc/callback"),
    ("post", "/v1/sso/saml/acs"),
    ("post", "/v1/chat/threads/{thread_id}/turns/stream"),
    ("post", "/v1/chat/ask"),  # the Ask answer stream
    ("get", "/v1/accounts/{domain}/deliverables/download"),  # a file or a zip
}


def _success_schema(op: dict[str, Any]) -> dict[str, Any]:
    for code in ("200", "201", "202"):
        found = op.get("responses", {}).get(code)
        if found:
            schema: dict[str, Any] = (
                found.get("content", {}).get("application/json", {}).get("schema", {})
            )
            return schema
    return {}


def test_every_json_operation_has_a_typed_response() -> None:
    doc = export_openapi.build()
    untyped = []
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if (method, path) in NOT_JSON:
                continue
            codes = set(op.get("responses", {}))
            if "204" in codes and not codes & {"200", "201", "202"}:
                continue  # a 204 has no body to type
            schema = _success_schema(op)
            typed = "$ref" in schema or schema.get("type") == "object"
            if not typed:
                untyped.append(f"{method.upper()} {path}")
    assert untyped == []


def test_the_contract_closes_its_objects() -> None:
    doc = export_openapi.build()
    schemas = doc["components"]["schemas"]
    from account_intelligence.api import contract

    for name in dir(contract):
        model = getattr(contract, name)
        closed = isinstance(model, type) and issubclass(model, contract.Out)
        if closed and model is not contract.Out and name in schemas:
            assert schemas[name].get("additionalProperties") is False, name


def test_the_committed_snapshot_has_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_generated_web_client_has_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_web_client.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
