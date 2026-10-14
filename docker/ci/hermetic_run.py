"""One hermetic research run against a running stack of the just-built images (CI only).

The stack runs with LLM_FAKE=hermetic and CONNECTORS=hermetic: the fake model and synthetic
providers, so the run makes no outside call and costs nothing. Staging refuses both switches by
design, so this check can never run there; it runs before deploy against the images that are
about to ship.

Steps: mint a synthetic token at the fake identity provider, start a run for a synthetic domain,
approve the credit cap if the run asks for it, poll until the run is terminal, and require
``completed``. Standard library only; no secret is read or printed (the token is synthetic).

Usage: hermetic_run.py --api http://127.0.0.1:8090 --idp http://127.0.0.1:9090 --tenant UUID
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

# Run statuses are upper case (account_runs.status). PARTIAL is "Complete with gaps": the fake
# model and synthetic providers leave some lanes thin, and the run still finishes and is graded.
TERMINAL = {"COMPLETED", "PARTIAL", "BUDGET_EXCEEDED", "FAILED", "CANCELED"}
PASSING = {"COMPLETED", "PARTIAL"}


def call(
    method: str, url: str, token: str | None = None, body: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    """One JSON request; returns (status, parsed body or {})."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if method == "POST" and url.endswith("/v1/runs"):
        headers["Idempotency-Key"] = uuid.uuid4().hex
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:  # noqa: S310 - http(s) only
            raw = reply.read()
            return reply.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw) if raw else {}
        except ValueError:
            return err.code, {}


def main() -> int:
    """Run the check; 0 on a completed run."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--idp", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--sub", default="ci-hermetic", help="token subject (a seeded member)")
    parser.add_argument("--role", default="analyst", help="must match the member's stored role")
    parser.add_argument("--domain", default="northwind.example")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if not args.domain.endswith(".example"):
        print("refusing a non-synthetic domain", file=sys.stderr)
        return 2

    status, minted = call(
        "POST",
        f"{args.idp}/token",
        body={"sub": args.sub, "tenant_id": args.tenant, "role": args.role, "mfa": True},
    )
    token = str(minted.get("access_token", ""))
    if status != 200 or not token:
        print(f"FAIL token from the fake identity provider: HTTP {status}", file=sys.stderr)
        return 1

    status, started = call(
        "POST", f"{args.api}/v1/runs", token, {"domain": args.domain, "depth": "standard"}
    )
    if status != 202:
        print(f"FAIL start run: HTTP {status} {started}", file=sys.stderr)
        return 1
    run = str(started["run_id"])
    print(f"started run {run}")

    deadline = time.monotonic() + args.timeout
    last = ""
    while time.monotonic() < deadline:
        status, state = call("GET", f"{args.api}/v1/runs/{run}", token)
        last = str(state.get("status", f"HTTP {status}")).upper()
        if last in TERMINAL:
            break
        if last in {"AWAITING_APPROVAL", "PAUSED"}:
            cap_status, cap = call("GET", f"{args.api}/v1/runs/{run}/cap", token)
            if cap_status == 200:
                call(
                    "POST",
                    f"{args.api}/v1/runs/{run}/cap/approve",
                    token,
                    {"version": cap.get("version"), "proposal_hash": cap.get("proposal_hash")},
                )
        time.sleep(3)
    print(f"run {run} finished with status {last}")
    return 0 if last in PASSING else 1


if __name__ == "__main__":
    sys.exit(main())
