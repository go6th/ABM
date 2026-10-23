"""Mail and drive go-live setup for one environment, in the order the providers need it.

You run this yourself, once, after creating the two OAuth apps (docs/deploy/RUNBOOK.md, "Mail and
drive go-live"). It never prints a secret and never puts one on a command line.

Step 1, ``configure``: asks for the domain the app will live on and the two client ids, then
  * writes ``domain`` and the ``maildrive`` block into infra/envs/<env>/terraform.tfvars
    (a git-ignored file; a backup is kept next to it),
  * prints the DNS record to add at your DNS provider and the redirect URI to register in both
    OAuth apps.
  Then deploy (terraform apply creates the two empty secrets and wires the ids).

Step 2, ``secrets``: asks for each client secret with hidden input and adds it as a new version of
  ``maildrive-google-client-secret`` / ``maildrive-microsoft-client-secret`` in Secret Manager,
  streamed to ``gcloud secrets versions add --data-file=-`` on stdin.

Step 3, ``check``: runs scripts/maildrive_selfcheck.py with the cloud rules.

    uv run --frozen python scripts/maildrive_setup.py configure --env staging
    uv run --frozen python scripts/maildrive_setup.py secrets --env staging
    uv run --frozen python scripts/maildrive_setup.py check --env staging
"""

from __future__ import annotations

import argparse
import getpass
import re
import shutil
import subprocess  # nosec B404 -- runs gcloud with fixed arguments; the secret goes on stdin
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
GOOGLE_ID_RE = re.compile(r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$")
MS_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SECRETS = {
    "google": "maildrive-google-client-secret",
    "microsoft": "maildrive-microsoft-client-secret",
}


def tfvars_path(env: str) -> Path:
    return ROOT / "infra" / "envs" / env / "terraform.tfvars"


def set_domain(text: str, domain: str) -> str:
    """``domain = "..."`` replaced (or added), keeping the rest of the file."""
    line = f'domain            = "{domain}"'
    if re.search(r"(?m)^domain\s*=", text):
        return re.sub(r"(?m)^domain\s*=.*$", line, text, count=1)
    return text.rstrip("\n") + "\n" + line + "\n"


def set_maildrive(text: str, google_id: str, microsoft_id: str) -> str:
    """The ``maildrive = { ... }`` block replaced (or added)."""
    block = (
        f'maildrive = {{ google_client_id = "{google_id}",'
        f' microsoft_client_id = "{microsoft_id}" }}'
    )
    if re.search(r"(?m)^maildrive\s*=", text):
        return re.sub(r"(?ms)^maildrive\s*=\s*\{.*?\}", block, text, count=1)
    return text.rstrip("\n") + "\n" + block + "\n"


def _ask(prompt: str, pattern: re.Pattern[str], *, optional: bool = False) -> str:
    while True:
        value = input(prompt).strip()
        if optional and not value:
            return ""
        if pattern.match(value):
            return value
        print("  That does not look right; try again (or Ctrl+C to stop).")


def configure(env: str) -> int:
    path = tfvars_path(env)
    if not path.exists():
        print(f"{path} does not exist; copy terraform.tfvars.example first.")
        return 1
    domain = _ask("Domain the app will live on (e.g. app.6th.ai): ", DOMAIN_RE)
    google_id = _ask(
        "Google OAuth client id (…apps.googleusercontent.com; blank to skip): ",
        GOOGLE_ID_RE,
        optional=True,
    )
    microsoft_id = _ask(
        "Microsoft application (client) id (a GUID; blank to skip): ", MS_ID_RE, optional=True
    )
    text = path.read_text()
    backup = path.with_suffix(f".tfvars.bak-{datetime.now(UTC):%Y%m%d%H%M%S}")
    shutil.copy2(path, backup)
    path.write_text(set_maildrive(set_domain(text, domain), google_id, microsoft_id))
    ip = _web_ip(env)
    print(f"\nUpdated {path} (backup: {backup.name}).\n")
    print("1. Add this DNS record at your DNS provider:")
    print(f"     {domain}.  A  {ip or '<the ai-' + env + '-web-ip address>'}")
    print("2. Register this redirect URI in BOTH OAuth apps:")
    print(f"     https://{domain}/v1/mail-and-drive/callback")
    print("   and on the Google consent screen: home page https://" + domain + "/,")
    print(f"   privacy policy https://{domain}/privacy, terms https://{domain}/terms.")
    print("3. Deploy (terraform apply) so the secrets exist and the ids are wired, then run:")
    print(f"     uv run --frozen python scripts/maildrive_setup.py secrets --env {env}")
    return 0


def _web_ip(env: str) -> str | None:
    if not shutil.which("gcloud"):
        return None
    done = subprocess.run(  # nosec B603 B607 -- fixed arguments
        [
            "gcloud",
            "compute",
            "addresses",
            "list",
            f"--filter=name=ai-{env}-web-ip",
            "--format=value(address)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() or None


def secrets(env: str) -> int:
    if not shutil.which("gcloud"):
        print("gcloud is not installed or not on PATH.")
        return 1
    text = tfvars_path(env).read_text()
    wanted = {
        "google": bool(re.search(r'google_client_id\s*=\s*"[^"]+"', text)),
        "microsoft": bool(re.search(r'microsoft_client_id\s*=\s*"[^"]+"', text)),
    }
    if not any(wanted.values()):
        print("No client id in terraform.tfvars yet: run `configure` first.")
        return 1
    for provider, needed in wanted.items():
        if not needed:
            continue
        name = SECRETS[provider]
        value = getpass.getpass(f"{provider.capitalize()} client secret (hidden; blank to skip): ")
        if not value.strip():
            continue
        done = subprocess.run(  # nosec B603 B607 -- fixed arguments; the secret is on stdin
            ["gcloud", "secrets", "versions", "add", name, "--data-file=-"],
            input=value.strip(),
            text=True,
            capture_output=True,
            check=False,
        )
        if done.returncode != 0:
            print(f"  {name}: not stored ({done.stderr.strip().splitlines()[-1:] or 'error'}).")
            print("  Has the deploy created the secret yet? (terraform apply after `configure`.)")
            return 1
        print(f"  {name}: new version stored.")
    print("Restart the api and worker services (or deploy) so they read the new versions, then:")
    print(f"  uv run --frozen python scripts/maildrive_setup.py check --env {env}")
    return 0


def _gcloud_ok(*args: str) -> bool:
    done = subprocess.run(  # nosec B603 B607 -- fixed arguments
        ["gcloud", *args], capture_output=True, text=True, check=False
    )
    return done.returncode == 0 and bool(done.stdout.strip())


def _value(text: str, pattern: str) -> str:
    found = re.search(pattern, text)
    return found.group(1) if found else ""


def check(env: str) -> int:
    """From an operator machine: the ids are configured, each has a secret version, the domain
    points at the load balancer and serves the legal pages and the callback, and the providers'
    endpoints match the config."""
    import socket

    import httpx

    text = tfvars_path(env).read_text()
    domain = _value(text, r'(?m)^domain\s*=\s*"([^"]+)"')
    ids = {
        "google": _value(text, r'google_client_id\s*=\s*"([^"]*)"'),
        "microsoft": _value(text, r'microsoft_client_id\s*=\s*"([^"]*)"'),
    }
    results: list[tuple[str, bool, str]] = []
    for provider, client_id in ids.items():
        results.append(
            (f"{provider} client id configured", bool(client_id), client_id or "missing")
        )
        if client_id and shutil.which("gcloud"):
            has = _gcloud_ok(
                "secrets",
                "versions",
                "list",
                SECRETS[provider],
                "--limit=1",
                "--filter=state=ENABLED",
                "--format=value(name)",
            )
            results.append(
                (f"{SECRETS[provider]} has a version", has, "run `secrets`" if not has else "ok")
            )
    wild = domain.endswith((".sslip.io", ".nip.io"))
    results.append(
        ("own domain (Google can verify it)", bool(domain) and not wild, domain or "missing")
    )
    expected = _web_ip(env)
    try:
        resolved = socket.gethostbyname(domain) if domain else ""
    except OSError:
        resolved = ""
    results.append(
        (
            "DNS points at the load balancer",
            bool(resolved) and resolved == expected,
            f"{domain} -> {resolved or 'nothing'} (want {expected})",
        )
    )
    for path in ("/privacy", "/terms"):
        code: int | str
        try:
            code = httpx.get(f"https://{domain}{path}", timeout=15).status_code
        except Exception as error:  # noqa: BLE001 - reported as a failed check
            code = type(error).__name__
        results.append((f"https://{domain}{path} is public", code == 200, str(code)))
    cb: int | str
    try:
        cb = httpx.get(
            f"https://{domain}/v1/mail-and-drive/callback", timeout=15, follow_redirects=False
        ).status_code
    except Exception as error:  # noqa: BLE001 - reported as a failed check
        cb = type(error).__name__
    # signed out, the callback answers 401 (it needs the user's session): reachable is the point
    results.append(("callback route reachable", cb in (401, 303), str(cb)))
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    endpoints = subprocess.run(  # nosec B603 -- fixed arguments
        [sys.executable, str(ROOT / "scripts" / "maildrive_selfcheck.py"), "--network", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    discovery_ok = '"endpoints match discovery", "ok": false' not in endpoints.stdout.replace(
        "\n", ""
    )
    print(f"[{'PASS' if discovery_ok else 'FAIL'}] provider endpoints match discovery")
    ready = all(ok for _, ok, _ in results) and discovery_ok
    print("READY: connect a test user and run the RUNBOOK smoke test." if ready else "NOT READY")
    return 0 if ready else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=["configure", "secrets", "check"])
    parser.add_argument("--env", default="staging")
    args = parser.parse_args(argv)
    return {"configure": configure, "secrets": secrets, "check": check}[args.step](args.env)


if __name__ == "__main__":
    sys.exit(main())
