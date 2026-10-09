#!/usr/bin/env python3
"""Report local toolchain and full-stack readiness without printing environment values.

Checks, each PASS/FAIL with a reason (never a value):

* tools: uv, Docker, Docker Compose, Node.js, npm;
* environment: the ignored ``.env`` (when present) validates against the typed registry
  (``account_intelligence.config``) for the ``dev`` profile;
* stack: every compose service is running (healthy where it has a health check) and the
  one-shot ``migrate`` exited 0; Postgres accepts a connection (no host ``pg_isready`` needed);
* endpoints: API ``/readyz``, worker ``/healthz``, web ``/login``, fake IdP discovery;
* auth round trip: a fake-IdP token for the fixed SYNTHETIC member (``doctor`` of the tenant
  ``LOCAL_TENANT``, created idempotently in the local database) is answered 200, and a token for
  a non-member is refused 401 (signature, issuer, audience and the membership re-check).

Exit 0 only when every check passes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TENANT = "00000000-0000-4000-8000-00000000d0c7"
SERVICES = ("db", "gcs", "tasks", "redis", "api", "worker", "web", "fake-idp")
Check = tuple[bool, str]


def _run(name: str, args: list[str]) -> Check:
    exe = shutil.which(args[0])
    if exe is None:
        return False, f"{name}: missing {args[0]}"
    result = subprocess.run([exe, *args[1:]], capture_output=True, text=True, check=False)
    if result.returncode:
        return False, f"{name}: command failed"
    version = (result.stdout or result.stderr).strip().splitlines()
    return True, f"{name}: {version[0] if version else 'available'}"


def _http(
    name: str, url: str, *, accept: set[int] | None = None, headers: dict[str, str] | None = None
) -> Check:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:  # nosec B310 - local URLs only
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (OSError, TimeoutError):
        return False, f"{name}: unavailable"
    return status in (accept or {200}), f"{name}: HTTP {status}"


def _dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def environment() -> Check:
    """The ignored .env validates for the dev profile (names only on failure)."""
    sys.path.insert(0, str(ROOT / "src"))
    from account_intelligence.config import EnvironmentInvalid, load_settings

    path = ROOT / ".env"
    if not path.exists():
        return True, "Environment: no .env (the hermetic stack needs none)"
    env = {"APP_ENV": "dev", **_dotenv(path)}
    try:
        load_settings(env)
    except EnvironmentInvalid as exc:
        return False, f"Environment: .env {exc}"
    return True, "Environment: .env validates against the registry"


def services() -> list[Check]:
    """Every service up (healthy where checked); migrate exited 0."""
    result = subprocess.run(
        ["docker", "compose", "ps", "--all", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if result.returncode:
        return [(False, "Compose: cannot list services")]
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    by_name = {row["Service"]: row for row in rows}
    out: list[Check] = []
    for name in SERVICES:
        row = by_name.get(name)
        if row is None:
            out.append((False, f"Service {name}: not created (run `make up`)"))
            continue
        health = row.get("Health") or ""
        ok = row.get("State") == "running" and health in ("", "healthy")
        out.append((ok, f"Service {name}: {row.get('State')}{' ' + health if health else ''}"))
    mig = by_name.get("migrate")
    ok = mig is not None and mig.get("State") == "exited" and int(mig.get("ExitCode", 1)) == 0
    out.append((ok, "Service migrate: " + ("exited 0" if ok else "did not complete")))
    return out


def postgres() -> Check:
    """A real connection (replaces the host pg_isready binary)."""
    try:
        import psycopg

        with psycopg.connect(
            "postgresql://postgres:dev@localhost:5432/ai", connect_timeout=3
        ) as conn:
            conn.execute("SELECT 1")
    except Exception:
        return False, "PostgreSQL: connection refused"
    return True, "PostgreSQL: accepting connections"


def _synthetic_member() -> None:
    """The fixed synthetic tenant and its viewer (local database only, idempotent)."""
    import psycopg

    with psycopg.connect("postgresql://postgres:dev@localhost:5432/ai", autocommit=True) as c:
        c.execute(
            "INSERT INTO tenants (id, name) VALUES (%s, 'doctor-synthetic') ON CONFLICT DO NOTHING",
            (LOCAL_TENANT,),
        )
        c.execute(
            "INSERT INTO memberships (tenant_id, user_id, role, status) VALUES"
            " (%s, 'doctor', 'viewer', 'active') ON CONFLICT (tenant_id, user_id) DO NOTHING",
            (LOCAL_TENANT,),
        )


def _token(sub: str, tenant: str) -> str:
    body = json.dumps({"sub": sub, "tenant_id": tenant, "role": "viewer"}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:9090/token",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as response:  # nosec B310 - local URL
        return str(json.loads(response.read())["access_token"])


def auth_round_trip() -> list[Check]:
    """A member's token is answered 200; a non-member's is refused 401."""
    try:
        _synthetic_member()
        member, stranger = _token("doctor", LOCAL_TENANT), _token("nobody", str(uuid.uuid4()))
    except Exception:
        return [(False, "Auth round trip: could not seed the member or mint tokens")]
    url = "http://127.0.0.1:8080/v1/accounts"
    return [
        _http("Auth member token", url, headers={"Authorization": f"Bearer {member}"}),
        _http(
            "Auth non-member token refused",
            url,
            accept={401},
            headers={"Authorization": f"Bearer {stranger}"},
        ),
    ]


def main() -> int:
    """Run every check; exit 0 only if all pass."""
    checks: list[Check] = [
        _run("uv", ["uv", "--version"]),
        _run("Docker", ["docker", "--version"]),
        _run("Docker Compose", ["docker", "compose", "version"]),
        _run("Node.js", ["node", "--version"]),
        _run("npm", ["npm", "--version"]),
        environment(),
        *services(),
        postgres(),
        _http("API readiness", "http://127.0.0.1:8080/readyz"),
        _http("Worker health", "http://127.0.0.1:8081/healthz"),
        _http("Web login route", "http://127.0.0.1:3000/login"),
        _http("Fake IdP discovery", "http://127.0.0.1:9090/.well-known/openid-configuration"),
        *auth_round_trip(),
    ]
    for ok, message in checks:
        print(f"{'PASS' if ok else 'FAIL'} {message}")
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
