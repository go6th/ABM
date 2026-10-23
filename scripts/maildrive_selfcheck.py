"""Mail and drive: check that an environment is ready for people to connect Gmail, Google Drive,
Outlook, OneDrive and SharePoint, before anyone tries.

It reads the same settings the API reads and checks, in order:

1. the OAuth apps: each provider's client id and client secret are set (a secret may come from a
   ``..._SECRET_FILE``); a provider without both shows "Not available yet" in the product;
2. the redirect URI the providers must have registered: ``PUBLIC_BASE_URL`` +
   ``/v1/mail-and-drive/callback`` (https in the cloud);
3. the scopes in config/connectors.yaml are the read-only ones (never a write scope);
4. with ``--network``: the providers' public OpenID discovery documents answer and name the
   authorize and token endpoints this config uses (no credential is sent);
5. with ``--api URL --cookie SESSION``: ``GET /v1/mail-and-drive`` on a running API answers and
   lists every source with its status (a signed-in session cookie of a test user).

Secrets are never printed: only whether they are set and their length class. Exit 0 when every
check that ran passed, 1 otherwise; ``--json`` prints the result for CI.

    uv run --frozen python scripts/maildrive_selfcheck.py
    uv run --frozen python scripts/maildrive_selfcheck.py --network
    uv run --frozen python scripts/maildrive_selfcheck.py --api https://HOST --cookie "$COOKIE"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("gmail", "google_drive", "outlook", "onedrive", "sharepoint")
PROVIDER = {
    "gmail": "google",
    "google_drive": "google",
    "outlook": "microsoft",
    "onedrive": "microsoft",
    "sharepoint": "microsoft",
}
# read-only scopes per source; anything else in the config is a finding
READ_ONLY = {
    "gmail": {"https://www.googleapis.com/auth/gmail.readonly"},
    "google_drive": {"https://www.googleapis.com/auth/drive.readonly"},
    "outlook": {"offline_access", "https://graph.microsoft.com/Mail.Read"},
    "onedrive": {"offline_access", "https://graph.microsoft.com/Files.Read"},
    "sharepoint": {"offline_access", "https://graph.microsoft.com/Sites.Read.All"},
}
DISCOVERY = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "microsoft": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
}
CALLBACK = "/v1/mail-and-drive/callback"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, fix: str = "") -> None:
        self.checks.append(Check(name, ok, detail, "" if ok else fix))

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _secret(env: dict[str, str], provider: str) -> str:
    prefix = f"MAILDRIVE_{provider.upper()}_CLIENT"
    value = env.get(f"{prefix}_SECRET", "")
    path = env.get(f"{prefix}_SECRET_FILE", "")
    if not value and path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return value


def check_apps(report: Report, env: dict[str, str]) -> set[str]:
    """Which providers have a registered app (id and secret)."""
    ready: set[str] = set()
    for provider in ("google", "microsoft"):
        client_id = env.get(f"MAILDRIVE_{provider.upper()}_CLIENT_ID", "")
        secret = _secret(env, provider)
        ok = bool(client_id and secret)
        sources = ", ".join(k for k in KINDS if PROVIDER[k] == provider)
        detail = (
            f"client id set ({len(client_id)} chars), secret set"
            if ok
            else f"client id {'set' if client_id else 'missing'},"
            f" secret {'set' if secret else 'missing'}"
        )
        report.add(
            f"{provider} OAuth app ({sources})",
            ok,
            detail,
            f"Register the {provider} OAuth app (docs/deploy/RUNBOOK.md 1.5); set "
            f"MAILDRIVE_{provider.upper()}_CLIENT_ID and the maildrive-{provider}-client-secret "
            "secret; until then these sources show 'Not available yet'.",
        )
        if ok:
            ready.add(provider)
    return ready


def check_redirect(report: Report, env: dict[str, str], *, cloud: bool) -> str | None:
    base = env.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        report.add(
            "redirect URI",
            False,
            "PUBLIC_BASE_URL is not set",
            "Set PUBLIC_BASE_URL to the web origin (connect answers 503 without it).",
        )
        return None
    url = base + CALLBACK
    parts = urlsplit(url)
    secure = parts.scheme == "https" or (not cloud and parts.hostname in ("localhost", "127.0.0.1"))
    report.add(
        "redirect URI",
        secure,
        url,
        "Use an https origin in the cloud; providers refuse http redirect URIs except localhost.",
    )
    if cloud and parts.hostname and parts.hostname.endswith((".sslip.io", ".nip.io")):
        report.add(
            "redirect domain",
            False,
            parts.hostname,
            "Google cannot verify an app on a wildcard DNS domain: use a domain you own.",
        )
    return url


def check_scopes(report: Report) -> None:
    raw = yaml.safe_load((ROOT / "config" / "connectors.yaml").read_text())["connectors"]
    for kind in KINDS:
        spec = raw.get(kind) or {}
        scopes = set(spec.get("scopes") or [])
        extra = scopes - READ_ONLY[kind]
        missing = READ_ONLY[kind] - scopes
        ok = bool(spec.get("enabled")) and not extra and not missing
        detail = f"enabled={bool(spec.get('enabled'))} scopes={sorted(scopes)}"
        report.add(
            f"{kind} scopes are read-only",
            ok,
            detail,
            f"Keep exactly {sorted(READ_ONLY[kind])}"
            f" (extra: {sorted(extra)}, missing: {sorted(missing)}).",
        )


def check_network(report: Report) -> None:
    import httpx

    raw = yaml.safe_load((ROOT / "config" / "connectors.yaml").read_text())["connectors"]
    for provider, url in DISCOVERY.items():
        try:
            doc = httpx.get(url, timeout=10).json()
        except Exception as error:  # noqa: BLE001 - reported as a failed check, never raised
            report.add(
                f"{provider} discovery",
                False,
                type(error).__name__,
                "Check outbound network access from this machine.",
            )
            continue
        kinds = [k for k in KINDS if PROVIDER[k] == provider]
        want_auth = {str(raw[k]["authorize_endpoint"]) for k in kinds}
        want_token = {str(raw[k].get("token_endpoint", "")) for k in kinds} - {""}
        # Microsoft's discovery names the {tenantid} form; /common is the multi-tenant alias
        got_auth = str(doc.get("authorization_endpoint", "")).replace("{tenantid}", "common")
        got_token = str(doc.get("token_endpoint", "")).replace("{tenantid}", "common")
        ok = want_auth == {got_auth} and (not want_token or want_token == {got_token})
        report.add(
            f"{provider} endpoints match discovery",
            ok,
            f"authorize={got_auth} token={got_token}",
            f"config/connectors.yaml should use {got_auth} and {got_token}.",
        )


def check_api(report: Report, api: str, cookie: str) -> None:
    import httpx

    try:
        reply = httpx.get(
            f"{api.rstrip('/')}/v1/mail-and-drive",
            headers={"Cookie": cookie, "Accept": "application/json"},
            timeout=15,
        )
    except Exception as error:  # noqa: BLE001 - reported as a failed check, never raised
        report.add("API answers", False, type(error).__name__, "Is the API reachable from here?")
        return
    report.add(
        "API answers",
        reply.status_code == 200,
        f"HTTP {reply.status_code}",
        "200 expected for a signed-in user (401: the cookie is not a live session).",
    )
    if reply.status_code != 200:
        return
    body: Any = reply.json()
    sources = body.get("sources") or body.get("items") or []
    seen = {str(s.get("kind")): str(s.get("status")) for s in sources if isinstance(s, dict)}
    report.add(
        "every source listed",
        set(KINDS) <= set(seen),
        ", ".join(f"{k}={v}" for k, v in sorted(seen.items())) or "none",
        "GET /v1/mail-and-drive should list all five sources.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--network", action="store_true", help="check the providers' public discovery documents"
    )
    parser.add_argument(
        "--api", help="base URL of a running web origin to call GET /v1/mail-and-drive"
    )
    parser.add_argument(
        "--cookie", default="", help="Cookie header of a signed-in test session (with --api)"
    )
    parser.add_argument(
        "--cloud", action="store_true", help="apply the cloud rules (https, own domain)"
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    report = Report()
    check_apps(report, env)
    check_redirect(report, env, cloud=args.cloud)
    check_scopes(report)
    if args.network:
        check_network(report)
    if args.api:
        check_api(report, args.api, args.cookie)

    if args.json:
        print(json.dumps({"ok": report.ok, "checks": [asdict(c) for c in report.checks]}, indent=2))
    else:
        for c in report.checks:
            print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
            if c.fix:
                print(f"       fix: {c.fix}")
        print("READY" if report.ok else "NOT READY")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
