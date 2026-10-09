#!/usr/bin/env python3
"""Report local tool and synthetic-stack readiness without printing environment values."""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def _run(name: str, args: list[str]) -> tuple[bool, str]:
    exe = shutil.which(args[0])
    if exe is None:
        return False, f"{name}: missing {args[0]}"
    result = subprocess.run([exe, *args[1:]], capture_output=True, text=True, check=False)
    if result.returncode:
        return False, f"{name}: command failed"
    version = (result.stdout or result.stderr).strip().splitlines()
    return True, f"{name}: {version[0] if version else 'available'}"


def _http(name: str, url: str, *, expected: set[int] | None = None) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (OSError, TimeoutError):
        return False, f"{name}: unavailable"
    accepted = expected or {200}
    return status in accepted, f"{name}: HTTP {status}"


def main() -> int:
    checks: list[tuple[bool, str]] = [
        _run("uv", ["uv", "--version"]),
        _run("Docker", ["docker", "--version"]),
        _run("Docker Compose", ["docker", "compose", "version"]),
        _run("Node.js", ["node", "--version"]),
        _run("npm", ["npm", "--version"]),
    ]
    checks.append(
        _run(
            "PostgreSQL client",
            ["pg_isready", "-h", "localhost", "-p", "5432", "-U", "postgres", "-d", "ai"],
        )
    )
    checks.extend(
        [
            _http("API readiness", "http://127.0.0.1:8080/readyz"),
            _http("Web login route", "http://127.0.0.1:3000/login"),
            _http("Fake IdP discovery", "http://127.0.0.1:9090/.well-known/openid-configuration"),
        ]
    )
    for ok, message in checks:
        print(f"{'PASS' if ok else 'FAIL'} {message}")
    print(
        "INFO Migration-current, authenticated proxy, seeded-role login, and LIVE key checks "
        "are not implemented by this doctor."
    )
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
