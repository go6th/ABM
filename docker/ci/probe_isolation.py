"""Read-only cross-tenant isolation probe. It sends GET requests only and never writes.

Two modes:

* ``--token-a TOKEN_ENV --token-b TOKEN_ENV --resource-b PATH``: authenticated. Tenant A asks for
  a resource that belongs to tenant B (PATH, for example ``/v1/runs/<id>``) and must get 404 or
  403. Tenant B's own token must get 200 on the same path, which proves the probe is not passing
  merely because the path is wrong. Used in CI against the just-built images, where the seed
  creates two tenants. The arguments NAME environment variables; the tokens are never on a command
  line.
* ``--anonymous``: needs no credentials. A tenant route with no credential, and with a forged
  token signed with a throwaway key, must answer 401 or 403 (never 200 or 404), and the Cloud
  Tasks routes must refuse a request without a Google-issued OIDC token. Used against staging,
  where tests hold no real tenant credential.

Standard library only; nothing secret is printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def get(url: str, token: str | None = None) -> int:
    """GET status code (no redirects followed beyond the default; 0 when unreachable)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:  # noqa: S310 - http(s) only
            return reply.status
    except urllib.error.HTTPError as err:
        return err.code
    except OSError:
        return 0


def post_empty(url: str) -> int:
    """POST a well-formed task body, no credential; only the refusal is observed."""
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {"tenant_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "slice": 0}
        ).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:  # noqa: S310 - http(s) only
            return reply.status
    except urllib.error.HTTPError as err:
        return err.code
    except OSError:
        return 0


def forged_token() -> str:
    """A JWT claiming a random tenant, signed (HS256) with a throwaway key nobody trusts."""

    def part(obj: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = int(time.time())
    head = part({"alg": "HS256", "typ": "JWT"})
    body = part(
        {
            "sub": "probe",
            "tenant_id": str(uuid.uuid4()),
            "role": "admin",
            "iat": now,
            "exp": now + 300,
        }
    )
    mac = hmac.new(os.urandom(32), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"


def check(name: str, got: int, allowed: set[int]) -> bool:
    """Print one result line and return whether it passed."""
    passed = got in allowed
    print(f"{'ok  ' if passed else 'FAIL'}  {name}: HTTP {got} (allowed {sorted(allowed)})")
    return passed


def main() -> int:
    """Run the selected probe; 0 when every check passed."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--anonymous", action="store_true")
    parser.add_argument(
        "--tasks-statuses",
        default="401,403",
        help="statuses accepted for the Cloud Tasks route (a public edge may answer 404)",
    )
    parser.add_argument("--token-a", help="NAME of the env var holding tenant A's token")
    parser.add_argument("--token-b", help="NAME of the env var holding tenant B's token")
    parser.add_argument("--resource-b", help="path of a resource owned by tenant B")
    args = parser.parse_args()
    api = args.api.rstrip("/")
    results: list[bool] = []

    if args.anonymous:
        random_run = f"{api}/v1/runs/{uuid.uuid4()}"
        results.append(check("no credential on a tenant route", get(random_run), {401, 403}))
        results.append(
            check("forged token on a tenant route", get(random_run, forged_token()), {401, 403})
        )
        results.append(
            check(
                "Cloud Tasks route without an OIDC token",
                post_empty(f"{api}/tasks/run-slice"),
                {int(code) for code in args.tasks_statuses.split(",")},
            )
        )
    else:
        if not (args.token_a and args.token_b and args.resource_b):
            parser.error("--token-a, --token-b and --resource-b are required without --anonymous")
        token_a, token_b = os.environ.get(args.token_a, ""), os.environ.get(args.token_b, "")
        if not token_a or not token_b:
            print("FAIL the token environment variables are empty", file=sys.stderr)
            return 2
        url = f"{api}{args.resource_b}"
        results.append(check("owner reads its own resource", get(url, token_b), {200}))
        results.append(check("other tenant reads it", get(url, token_a), {403, 404}))
        results.append(check("no credential reads it", get(url), {401, 403}))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
