"""Reconcile frontend API call sites with the generated backend OpenAPI contract.

This is a static audit, not a live-stack or authentication test. It reuses the API snapshot,
generated-client drift checks and route-policy catalog rather than introducing another source of
truth. ``--check`` verifies the generated report without changing files.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_route_catalog import problems as route_policy_problems  # noqa: E402
from export_openapi import build as build_openapi  # noqa: E402

OUTPUT = ROOT / "docs" / "WIRING.md"
METHODS = r"GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"
STRING = r'(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`)'
CALLS: tuple[tuple[str, str], ...] = (
    (
        "tenant client",
        rf"\bapi(?:<[^>\n]*>)?\s*\(\s*\"(?P<method>{METHODS})\"\s*,\s*(?P<path>{STRING})",
    ),
    ("tenant page query", rf"\buseApi(?:<[^>\n]*>)?\s*\(\s*(?P<path>{STRING})"),
    (
        "dynamic tenant wrapper",
        rf"\bcall\s*\(\s*\"(?P<method>{METHODS})\"\s*,\s*(?P<path>{STRING})",
    ),
    ("same-origin or external fetch", rf"\bfetch\s*\(\s*(?P<path>{STRING})"),
    ("server API fetch", rf"\bfetch\s*\(\s*new\s+URL\(\s*(?P<path>{STRING})"),
    ("progress event stream", rf"\bnew\s+EventSource\(\s*(?P<path>{STRING})"),
)
GENERIC_HELPERS = {
    "web/lib/api.ts",
    "web/lib/useApi.ts",
    "web/lib/operator-api.ts",
    "web/app/api/operator/v1/[...path]/route.ts",
}


@dataclass(frozen=True)
class FrontendCall:
    source: str
    kind: str
    method: str
    path: str


def _literal(value: str) -> str:
    if value.startswith("`"):
        return value[1:-1]
    if value.startswith("'"):
        parsed = ast.literal_eval(value)
        return parsed if isinstance(parsed, str) else value
    return json.loads(value)


def _path_from_literal(value: str) -> str:
    """Return the route portion of a literal, dropping a server origin and query string."""
    if value.startswith(("http://", "https://")):
        return value.split("?", 1)[0]
    value = re.sub(r"\$\{[^}]*\}", "sample", value)
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        value = parsed.path
    if not value.startswith("/"):
        match = re.search(r"/(?:v1|operator/v1|api)/[^\s`\"']*", value)
        if not match:
            return value
        value = match.group(0)
    return value.split("?", 1)[0]


def _route_matches(template: str, path: str) -> bool:
    parts: list[str] = []
    for segment in template.split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(segment))
    return re.fullmatch("/".join(parts), path) is not None


def frontend_calls() -> list[FrontendCall]:
    """Find literal API call sites in app, component and library source files."""
    found: set[FrontendCall] = set()
    for folder in (ROOT / "web" / "app", ROOT / "web" / "components", ROOT / "web" / "lib"):
        for path in sorted((*folder.rglob("*.ts"), *folder.rglob("*.tsx"))):
            relative = path.relative_to(ROOT).as_posix()
            if relative in GENERIC_HELPERS or "/gen/" in relative:
                continue
            source = path.read_text()
            for match in re.finditer(rf'"(?P<key>(?:{METHODS}) /operator/v1/[^\"]+)"', source):
                method, route = match.group("key").split(" ", 1)
                found.add(
                    FrontendCall(relative, "generated operator operation reference", method, route)
                )
            for kind, pattern in CALLS:
                for match in re.finditer(pattern, source):
                    if "key" in match.groupdict() and match.group("key"):
                        method, route = match.group("key").split(" ", 1)
                    else:
                        method = match.groupdict().get("method")
                        if method is None and kind == "same-origin or external fetch":
                            options = source[match.end() : match.end() + 240]
                            method_match = re.search(
                                r'\bmethod\s*:\s*"(?P<method>' + METHODS + r')"', options
                            )
                            method = method_match.group("method") if method_match else "GET"
                        method = method or "GET"
                        route = _path_from_literal(_literal(match.group("path")))
                    if route.startswith(("/", "http://", "https://")):
                        found.add(FrontendCall(relative, kind, method.upper(), route))
    return sorted(found, key=lambda call: (call.path, call.method, call.source, call.kind))


def _schema_name(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "—"
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    kind = schema.get("type")
    return f"inline {kind}" if kind else "inline schema"


def _request_info(operation: dict[str, Any]) -> tuple[str, bool]:
    request = operation.get("requestBody") or {}
    content = request.get("content", {})
    media = content.get("application/json") or next(iter(content.values()), {})
    schema = media.get("schema") if isinstance(media, dict) else None
    return _schema_name(schema), bool(request.get("required", False))


def _response_info(operation: dict[str, Any]) -> tuple[str, str]:
    for status in ("200", "201", "202", "204"):
        response = operation.get("responses", {}).get(status)
        if response is None:
            continue
        content = response.get("content", {})
        schema = content.get("application/json", {}).get("schema")
        if schema is None and content:
            media = next(iter(content.values()))
            schema = media.get("schema")
        return status, _schema_name(schema)
    return "—", "—"


def _policy_text(policy: Any) -> str:
    if policy is None:
        return "public health probe"
    plane = "operator" if "operator_support" in policy.principal_types else "tenant"
    principals = ", ".join(sorted(policy.principal_types))
    roles = ", ".join(sorted(policy.operator_roles or policy.roles)) or "—"
    return (
        f"{plane}; roles={roles}; principal={principals}; "
        f"tenant={policy.tenant_source}; audit={policy.audit}"
    )


def _file_link(path: str) -> str:
    return f"`{path}`"


def render() -> tuple[str, list[str]]:
    spec = build_openapi()
    from account_intelligence.api.policy import ROUTE_POLICIES

    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                operations[(method.upper(), path)] = operation

    calls = frontend_calls()
    breaks: list[str] = []
    mapped: list[tuple[FrontendCall, tuple[str, str] | None]] = []
    for call in calls:
        if call.path.startswith(("http://", "https://")):
            mapped.append((call, None))  # External identity-provider token exchange.
            continue
        if call.path.startswith("/api/"):
            mapped.append((call, None))  # Next.js same-origin backend-for-frontend route.
            continue
        candidates = [
            key for key in operations if key[0] == call.method and _route_matches(key[1], call.path)
        ]
        if candidates:
            specificity = max(
                sum(not (part.startswith("{") and part.endswith("}")) for part in key[1].split("/"))
                for key in candidates
            )
            candidates = [
                key
                for key in candidates
                if sum(
                    not (part.startswith("{") and part.endswith("}")) for part in key[1].split("/")
                )
                == specificity
            ]
        if len(candidates) == 1:
            mapped.append((call, candidates[0]))
        else:
            breaks.append(
                f"{call.method} {call.path} from {call.source} matched "
                f"{len(candidates)} backend operations"
            )
            mapped.append((call, None))

    policy_problems = route_policy_problems()
    breaks.extend(policy_problems)
    snapshot_check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    client_check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_web_client.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if snapshot_check.returncode:
        breaks.append("OpenAPI snapshot drift: scripts/export_openapi.py --check")
    if client_check.returncode:
        breaks.append("generated web client drift: scripts/gen_web_client.py --check")
    route_keys = set(operations)
    policy_keys = set(ROUTE_POLICIES)
    protected = {key for key in route_keys if key[1].startswith(("/v1/", "/operator/"))}
    missing_policy = sorted(protected - policy_keys)
    if missing_policy:
        breaks.extend(f"no route policy: {method} {path}" for method, path in missing_policy)

    used = {key for _, key in mapped if key is not None}
    report = [
        "# Frontend and API wiring audit",
        "",
        (
            "Generated by `scripts/wiring_audit.py`; run it again when frontend calls or API "
            "routes change."
        ),
        "",
        (
            "This is a static contract audit. It builds the FastAPI OpenAPI document without "
            "connecting"
        ),
        "to a database, reads frontend call sites, and reuses the existing route-policy catalog.",
        "It does not prove browser login, a running Compose stack, a live identity provider, or",
        "provider calls. Those require separate local-stack or externally configured checks.",
        "",
        "## Summary",
        "",
        f"- Backend operations in OpenAPI: **{len(operations)}**.",
        f"- Frontend literal call sites found: **{len(calls)}**.",
        (
            "- Frontend call sites mapped to backend operations: **"
            f"{sum(key is not None for _, key in mapped)}**."
        ),
        f"- Backend operations without a direct frontend call site: **{len(route_keys - used)}**.",
        f"- Route-policy/catalog violations: **{len(policy_problems) + len(missing_policy)}**.",
        f"- Unmapped frontend calls: **{sum('matched ' in item for item in breaks)}**.",
        "",
        "Uncalled backend operations remain part of the documented OpenAPI surface and may support",
        (
            "non-browser clients or later screens. This count is not treated as an orphan deletion "
            "list."
        ),
        (
            "The frontend call-site scanner handles literal operation names and route strings; "
            "indirect"
        ),
        "calls are listed only when their wrapper has a statically visible literal call site.",
        "",
        "## Frontend call sites",
        "",
        "| Source | Call type | Method and path | Request schema | Response schema | Access rule |",
        "|---|---|---|---|---|---|",
    ]
    for call, key in mapped:
        if key is None:
            if call.path.startswith("/api/"):
                request_schema, response_schema, policy = (
                    "Next.js route",
                    "Next.js route",
                    "staff session / same-origin",
                )
            elif call.path.startswith(("http://", "https://")):
                request_schema, response_schema, policy = (
                    "OAuth code exchange",
                    "OAuth token response",
                    "external identity provider; server-only",
                )
            else:
                request_schema, response_schema, policy = "unmapped", "unmapped", "unverified"
            method_path = f"`{call.method} {call.path}`"
        else:
            operation = operations[key]
            request_schema, request_required = _request_info(operation)
            if request_required and request_schema != "—":
                request_schema += " (required)"
            _, response_schema = _response_info(operation)
            policy = _policy_text(ROUTE_POLICIES.get(key))
            method_path = f"`{key[0]} {key[1]}`"
        report.append(
            f"| {_file_link(call.source)} | {call.kind} | {method_path} | "
            f"{request_schema} | {response_schema} | {policy} |"
        )

    report.extend(
        [
            "",
            "## Backend route inventory",
            "",
            "Schema names come from the live application OpenAPI document. Policy values come from",
            "`ROUTE_POLICIES`; health probes are public and do not expose tenant data.",
            "",
            "| Method and path | Request schema | Success schema | Access rule |",
            "|---|---|---|---|",
        ]
    )
    for key in sorted(operations):
        operation = operations[key]
        request_schema, request_required = _request_info(operation)
        if request_required and request_schema != "—":
            request_schema += " (required)"
        status, response_schema = _response_info(operation)
        if response_schema != "—":
            response_schema = f"{response_schema} ({status})"
        report.append(
            f"| `{key[0]} {key[1]}` | {request_schema} | {response_schema} | "
            f"{_policy_text(ROUTE_POLICIES.get(key))} |"
        )

    report.extend(
        [
            "",
            "## Existing contract gates",
            "",
            (
                "- `scripts/export_openapi.py --check` compares the committed OpenAPI snapshot "
                "with the app."
            ),
            (
                "- `scripts/gen_web_client.py --check` compares generated TypeScript and Zod "
                "schemas with the snapshot."
            ),
            (
                "- `scripts/check_route_catalog.py` verifies route policy coverage and "
                "tenant-source rules."
            ),
            (
                "- OpenAPI snapshot drift check: "
                f"**{'PASS' if snapshot_check.returncode == 0 else 'FAIL'}**."
            ),
            (
                "- Generated client drift check: "
                f"**{'PASS' if client_check.returncode == 0 else 'FAIL'}**."
            ),
            (
                "- The tenant browser helper now validates JSON request and successful response "
                "payloads against those generated schemas."
            ),
            (
                "- The operator client already validates successful JSON responses and its proxy "
                "admits only generated operator operations."
            ),
            "",
            "## Limits and remaining verification",
            "",
            (
                "- Browser-to-API login, CSRF, session expiry, logout and role-denial flows still "
                "require a running local stack and browser tests."
            ),
            (
                "- The current Compose file starts database/emulator services and the API; it does "
                "not start the Next.js app, a worker, a fake IdP, or provider stubs."
            ),
            (
                "- Live Google Workspace, customer IdPs, data providers and Google Cloud "
                "deployment "
                "are not exercised by this audit."
            ),
            (
                "- UI role visibility is not treated as authorization; the API policy catalog "
                "remains authoritative."
            ),
            "",
            "## Findings",
            "",
        ]
    )
    if breaks:
        report.extend(["Static breaks found:", ""])
        report.extend(f"- {problem}" for problem in breaks)
    else:
        report.append("- **No static frontend route breaks or route-policy gaps found.**")
    report.append("")
    return "\n".join(report), breaks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if docs/WIRING.md is stale")
    args = parser.parse_args()
    content, breaks = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            print("wiring report drift: run python scripts/wiring_audit.py", file=sys.stderr)
            return 1
        print("wiring audit report OK")
    else:
        OUTPUT.write_text(content)
        print(f"wrote {OUTPUT.relative_to(ROOT)}")
    if breaks:
        for item in breaks:
            print(f"wiring audit: {item}", file=sys.stderr)
        return 1
    print(f"wiring audit: {len(frontend_calls())} frontend call sites; no static breaks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
