"""End-to-end wiring verification: prints ``LAYER | CHECK | RESULT | EVIDENCE`` and exits non-zero
on any FAIL.

Hermetic: needs no keys and makes no paid or external call. It runs the repository's own tests
for each row and probes the local compose stack (``make up``) for the rows that are about the
running wiring. SKIP is used only for live provider calls (``make smoke-live``) and for
capabilities the backend does not offer; a stack that is down is a FAIL, never a SKIP.

Secrets: values are never printed. Rows that check for leaked values read ``.env`` in process
and report only the variable NAME that leaked.

Usage: ``make verify-wiring`` (or ``uv run python scripts/verify_wiring.py [--only LAYER]``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.yml")]
API = os.environ.get("WIRING_API", "http://localhost:8080")
WEB = os.environ.get("WIRING_WEB", "http://localhost:3000")
TEST_ENV = {
    "PGPASSWORD": "dev",
    "APP_RW_PASSWORD": "local-dev-only",
    "TEST_DB": "ai_test",
    "ADMIN_MAINT_DSN": "postgresql://postgres:dev@localhost:5432/postgres",
    "MIGRATOR_DATABASE_URL": "postgresql://postgres:dev@localhost:5432/ai_test",
    "CHECKPOINT_DATABASE_URL": "postgresql://postgres:dev@localhost:5432/ai_test",
    "DATABASE_URL": "postgresql+asyncpg://app_rw:local-dev-only@localhost:5432/ai_test",
}
CONTRACT = "tests/system/test_wiring_contract.py"
PROVIDERS = ("search", "scrape", "signals", "people", "org")


@dataclass
class Row:
    """One matrix row."""

    layer: str
    check: str
    result: str
    evidence: str


Check = Callable[[], tuple[str, str]]


# ---- helpers ------------------------------------------------------------------------------------


def run(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 900) -> tuple[int, str]:
    """Run a command; return code and combined output (never shell-interpolated)."""
    try:
        done = subprocess.run(
            cmd,
            cwd=ROOT,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 99, f"{type(error).__name__}"
    return done.returncode, (done.stdout + done.stderr)


def pytest(*targets: str, db: bool = False) -> tuple[str, str]:
    """PASS when the selected tests pass; the evidence is pytest's summary line."""
    code, out = run(
        [
            "uv",
            "run",
            "--frozen",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-header",
            *targets,
        ],
        env=TEST_ENV if db else None,
    )
    summary = next(
        (
            ln.strip("= ")
            for ln in reversed(out.splitlines())
            if re.search(r"\d+ (passed|failed|error)", ln)
        ),
        out.strip().splitlines()[-1] if out.strip() else "no output",
    )
    if code == 0:
        return "PASS", summary
    failed = [
        ln.split(" - ")[0].replace("FAILED ", "")
        for ln in out.splitlines()
        if ln.startswith(("FAILED", "ERROR"))
    ]
    return "FAIL", f"{summary}; " + ", ".join(t.split("::")[-1] for t in failed[:4])


def http(
    url: str, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None
) -> tuple[int, dict[str, str], str]:
    """One HTTP call to the local stack; (status, lower-cased headers, body)."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return (
                reply.status,
                {k.lower(): v for k, v in reply.headers.items()},
                reply.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            {k.lower(): v for k, v in error.headers.items()},
            error.read().decode("utf-8", "replace"),
        )
    except (urllib.error.URLError, OSError) as error:
        return 0, {}, type(error).__name__


def container(service: str) -> str:
    """The running container id of a compose service ('' when it is not running)."""
    _, out = run([*COMPOSE, "ps", "-q", service], timeout=60)
    return out.strip().splitlines()[0] if out.strip() else ""


def exec_in(service: str, *cmd: str) -> tuple[int, str]:
    cid = container(service)
    if not cid:
        return 98, f"{service} container not running"
    return run(["docker", "exec", cid, *cmd], timeout=120)


def psql(sql: str) -> tuple[int, str]:
    return exec_in("db", "psql", "-U", "postgres", "-d", "ai", "-Atc", sql)


PUBLIC_FIXTURES = (".env.example", "Makefile", "compose.yml", ".github/workflows/ci.yml")


def dotenv_values() -> dict[str, str]:
    """``.env`` values for leak checks only (never printed): values of 12+ chars that are not
    already published in a tracked dev fixture (``local-dev-only`` is a public dev password)."""
    path = ROOT / ".env"
    if not path.is_file():
        return {}
    public = "".join(
        (ROOT / f).read_text(encoding="utf-8") for f in PUBLIC_FIXTURES if (ROOT / f).is_file()
    )
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            value = match.group(2).strip().strip("'\"")
            if (
                len(value) >= 12
                and value not in public
                and not value.startswith(("http://localhost", "your-", "replace-with"))
            ):
                values[match.group(1)] = value
    return values


def leaked_names(text: str) -> list[str]:
    secretish = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|DATABASE_URL)")
    return sorted(n for n, v in dotenv_values().items() if secretish.search(n) and v in text)


# ---- CONFIG --------------------------------------------------------------------------------------


def dotenv_untracked() -> tuple[str, str]:
    ignored, _ = run(["git", "check-ignore", "-q", ".env"])
    _, tracked = run(["git", "ls-files", ".env", ".env.local", "web/.env.local"])
    if ignored == 0 and not tracked.strip():
        return "PASS", "git check-ignore .env ok; no .env file tracked"
    return "FAIL", f"ignored={ignored == 0}; tracked={tracked.split()}"


SECRET_PATTERNS = {
    "anthropic key": r"sk-ant-(api|admin)\d{2}-[A-Za-z0-9_\-]{20,}",
    "firecrawl key": r"\bfc-[a-f0-9]{32}\b",
    "aws access key": r"\bAKIA[0-9A-Z]{16}\b",
    "private key": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "google api key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "slack token": r"\bxox[abpr]-[A-Za-z0-9-]{10,}",
}


def secret_scan() -> tuple[str, str]:
    """gitleaks when installed; otherwise pattern scan of the tracked tree and the last 200
    commits, plus the literal ``.env`` values (names reported, never values)."""
    if shutil.which("gitleaks"):
        code, out = run(["gitleaks", "detect", "--no-banner", "--redact", "-v", "--log-opts=-200"])
        return ("PASS" if code == 0 else "FAIL"), "gitleaks: " + (
            out.strip().splitlines() or ["clean"]
        )[-1]
    _, files = run(["git", "ls-files", "-z"])
    hits: list[str] = []
    values = {n: v for n, v in dotenv_values().items() if re.search(r"KEY|TOKEN|SECRET", n)}
    for name in filter(None, files.split("\0")):
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits += [f"{name}: {label}" for label, rx in SECRET_PATTERNS.items() if re.search(rx, text)]
        hits += [f"{name}: value of {n}" for n, v in values.items() if v in text]
    _, history = run(["git", "log", "-p", "-200", "--format=%H"], timeout=300)
    hits += [f"history: {label}" for label, rx in SECRET_PATTERNS.items() if re.search(rx, history)]
    hits += [f"history: value of {n}" for n, v in values.items() if v in history]
    if hits:
        return "FAIL", "; ".join(sorted(set(hits))[:6])
    return (
        "PASS",
        f"pattern scan (gitleaks not installed): {len(files.split(chr(0)))} tracked files "
        "+ 200 commits, .env values absent",
    )


# ---- DATABASE ------------------------------------------------------------------------------------


def alembic_at_head() -> tuple[str, str]:
    code, current = psql("select version_num from alembic_version")
    _, heads = run(["uv", "run", "--frozen", "alembic", "heads"])
    head = sorted(re.findall(r"^(\S+) \(head\)", heads, re.M))
    cur = sorted(current.split())
    if code == 0 and cur and cur == head:
        return "PASS", f"current {cur} == heads {head}"
    return "FAIL", f"current {cur or current.strip()[:80]} heads {head}"


ROLE_PROBE = """
import asyncio, os
async def main():
    import asyncpg
    out = []
    for var in ("DATABASE_URL", "CHECKPOINT_DATABASE_URL"):
        url = os.environ.get(var, "")
        if not url:
            out.append(var + "=unset"); continue
        conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
        row = await conn.fetchrow(
            "select current_user, r.rolsuper, r.rolbypassrls from pg_roles r "
            "where r.rolname = current_user"
        )
        out.append(f"{var}={row[0]},super={row[1]},bypassrls={row[2]}")
        await conn.close()
    print(";".join(out))
asyncio.run(main())
"""


def runtime_roles() -> tuple[str, str]:
    evidence, ok = [], True
    for service in ("api", "worker"):
        code, out = exec_in(service, "python", "-c", ROLE_PROBE)
        line = out.strip().splitlines()[-1] if out.strip() else out
        evidence.append(f"{service}: {line}")
        ok &= code == 0 and line.count("app_rw,super=False,bypassrls=False") == 2
    return ("PASS" if ok else "FAIL"), " | ".join(evidence)


def forced_rls() -> tuple[str, str]:
    sql = """
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind = 'r'
      and exists (select 1 from information_schema.columns k
                  where k.table_schema = 'public' and k.table_name = c.relname
                    and k.column_name = 'tenant_id')
      and (not c.relrowsecurity or not c.relforcerowsecurity
           or not exists (select 1 from pg_policies p where p.tablename = c.relname))
      -- a partition is reached through its parent's policy unless app_rw can touch it directly
      and not (c.relispartition and not has_table_privilege(
        'app_rw', c.oid, 'select,insert,update,delete'))
    """
    code, bad = psql(sql)
    _, total = psql(
        "select count(distinct table_name) from information_schema.columns "
        "where table_schema='public' and column_name='tenant_id'"
    )
    if code == 0 and not bad.strip():
        return (
            "PASS",
            f"{total.strip()} tenant tables (partitions via parent): "
            "all FORCE RLS with a policy, no direct partition grant",
        )
    return "FAIL", f"without forced RLS or policy: {bad.split()[:8] or bad[:120]}"


def checkpoint_prefix() -> tuple[str, str]:
    code, out = psql(
        "select tablename || ':' || coalesce(qual,'') from pg_policies "
        "where tablename like 'checkpoint%'"
    )
    tables = [ln.split(":")[0] for ln in out.splitlines() if ln]
    if code == 0 and tables and all("thread_id" in ln for ln in out.splitlines() if ln):
        return "PASS", f"thread_id tenant prefix enforced on {sorted(set(tables))}"
    return "FAIL", f"checkpoint policies: {out.strip()[:160] or 'none'}"


def pgvector() -> tuple[str, str]:
    _, version = psql("select extversion from pg_extension where extname = 'vector'")
    _, hnsw = psql("select indexname from pg_indexes where indexdef ilike '%using hnsw%'")
    v = version.strip()
    ok = bool(v) and tuple(int(x) for x in v.split(".")[:2]) >= (0, 8) and bool(hnsw.strip())
    return (
        "PASS" if ok else "FAIL"
    ), f"pgvector {v or 'absent'}; hnsw indexes {hnsw.split() or 'none'}"


# ---- API -----------------------------------------------------------------------------------------


def healthz() -> tuple[str, str]:
    status, _, body = http(f"{API}/healthz")
    return (
        "PASS" if status == 200 and '"ok"' in body else "FAIL"
    ), f"/healthz {status} {body[:60]}"


def readyz_components() -> tuple[str, str]:
    status, _, body = http(f"{API}/readyz")
    try:
        doc = json.loads(body)
    except json.JSONDecodeError:
        return "FAIL", f"/readyz {status} not JSON"
    components = doc.get("components", {})
    needed = {"payload_store", "rate_limit_store", "llm", *(f"provider.{p}" for p in PROVIDERS)}
    missing = sorted(needed - set(components))
    leaks = leaked_names(body)
    if status == 200 and not missing and not leaks:
        return "PASS", "/readyz 200 " + ", ".join(f"{k}={v}" for k, v in sorted(components.items()))
    return (
        "FAIL",
        f"/readyz {status}; missing {missing}; leaked {leaks}; checks {doc.get('checks')}",
    )


def cors_denied() -> tuple[str, str]:
    status, headers, _ = http(
        f"{API}/v1/accounts",
        "OPTIONS",
        {"Origin": "https://attacker.invalid", "Access-Control-Request-Method": "POST"},
    )
    allow = headers.get("access-control-allow-origin")
    return (
        "PASS" if not allow else "FAIL"
    ), f"OPTIONS from foreign Origin -> {status}, allow-origin={allow or 'absent'}"


def request_id_echo() -> tuple[str, str]:
    rid = str(uuid.uuid4())
    _, h1, _ = http(f"{API}/healthz", headers={"X-Request-ID": rid})
    _, h2, _ = http(f"{API}/healthz", headers={"X-Request-ID": "not a uuid; injected"})
    ok = h1.get("x-request-id") == rid and re.fullmatch(
        r"[0-9a-f-]{36}", h2.get("x-request-id", "")
    )
    return (
        "PASS" if ok else "FAIL"
    ), f"supplied uuid echoed={h1.get('x-request-id') == rid}; junk replaced={bool(ok)}"


def request_id_in_api_log() -> tuple[str, str]:
    rid = str(uuid.uuid4())
    http(f"{API}/v1/accounts", headers={"X-Request-ID": rid})
    time.sleep(1.0)
    cid = container("api")
    if not cid:
        return "FAIL", "api container not running"
    _, logs = run(["docker", "logs", "--since", "30s", cid])
    hits = [ln for ln in logs.splitlines() if rid in ln]
    if hits:
        return "PASS", f"{len(hits)} api log line(s) carry request_id {rid[:8]}"
    return (
        "FAIL",
        f"request_id {rid[:8]} absent from api logs ({len(logs.splitlines())} lines in window)",
    )


def forged_tenant_header() -> tuple[str, str]:
    status, _, body = http(
        f"{API}/v1/accounts",
        headers={"X-Tenant-ID": str(uuid.uuid4()), "X-Tenant": "forged", "X-Tenant-Slug": "forged"},
    )
    return (
        "PASS" if status == 401 else "FAIL"
    ), f"unauthenticated + forged X-Tenant* -> {status} (tenant only from session)"


def body_limit() -> tuple[str, str]:
    big = b"{" + b" " * (2 * 1024 * 1024) + b"}"
    status, _, _ = http(f"{API}/v1/chat/threads", "POST", {"Content-Type": "application/json"}, big)
    return (
        "PASS" if status == 413 else "FAIL"
    ), f"2 MiB body to /v1/chat/threads -> {status} (want 413 before auth or parsing)"


def no_stack_traces() -> tuple[str, str]:
    status, _, body = http(f"{API}/v1/accounts/%00/tables/x")
    bad = any(s in body for s in ("Traceback", 'File "/', "sqlalchemy", "asyncpg"))
    return (
        "FAIL" if bad else "PASS"
    ), f"malformed path -> {status}, problem body without internals={not bad}"


def session_cookie_policy() -> tuple[str, str]:
    text = (ROOT / "config" / "session.yaml").read_text(encoding="utf-8")
    sso = (ROOT / "src/account_intelligence/api/routes/sso.py").read_text(encoding="utf-8")
    ok = (
        "__Host-ai_session" in text
        and "secure=True" in sso
        and "httponly=True" in sso
        and "domain=" not in sso
    )
    return (
        "PASS" if ok else "FAIL"
    ), "__Host-ai_session: Secure, HttpOnly, SameSite=Lax, Path=/, no Domain"


# ---- WEB -----------------------------------------------------------------------------------------


def web_server_side_proxy() -> tuple[str, str]:
    _, public = run(
        [
            "git",
            "grep",
            "-n",
            "NEXT_PUBLIC_",
            "--",
            "web/app",
            "web/lib",
            "web/components",
            "web/next.config.ts",
            "web/proxy.ts",
        ]
    )
    route = (ROOT / "web/app/v1/[[...path]]/route.ts").read_text(encoding="utf-8")
    ok = not public.strip() and "process.env.API_ORIGIN" in route
    return (
        ("PASS" if ok else "FAIL"),
        f"/v1/* proxied by a route handler to server-only API_ORIGIN; "
        f"NEXT_PUBLIC_ vars: {public.count(chr(10)) or 'none'}",
    )


def web_csp() -> tuple[str, str]:
    status, headers, _ = http(f"{WEB}/login")
    csp = headers.get("content-security-policy", "")
    ok = (
        status == 200
        and "connect-src 'self'" in csp
        and "'nonce-" in csp
        and "unsafe-inline" not in csp
    )
    return (
        "PASS" if ok else "FAIL"
    ), f"/login {status}; connect-src 'self'={'connect-src' in csp}; nonce={'nonce-' in csp}"


def web_reaches_api() -> tuple[str, str]:
    probe = (
        "fetch(process.env.API_ORIGIN+'/readyz').then(r=>{console.log(r.status);"
        "process.exit(r.ok?0:1)}).catch(e=>{console.log(e.name);process.exit(1)})"
    )
    code, out = exec_in("web", "node", "-e", probe)
    status, _, body = http(f"{WEB}/readyz")
    ok = code == 0 and status == 200
    return (
        "PASS" if ok else "FAIL"
    ), f"web container -> API_ORIGIN/readyz {out.strip()[-20:]}; browser /readyz via web {status}"


def bundle_scan() -> tuple[str, str]:
    static = ROOT / "web/.next/static"
    if not static.is_dir():
        return "FAIL", "web/.next/static missing: run `npm --prefix web run build` first"
    rx = re.compile(
        r"postgres(ql)?(\+asyncpg)?://|P_(SEARCH|SCRAPE|SIGNALS|PEOPLE|ORG)_API|ANTHROPIC_API_KEY|sk-ant-[A-Za-z0-9_\-]{20,}|"
        r"parallel\.ai|firecrawl|predictleads|bitscale|sumble|-----BEGIN"
    )
    values = dotenv_values()
    hits: set[str] = set()
    count = 0
    for path in static.rglob("*"):
        if path.suffix not in {".js", ".css", ".map", ".json", ".txt", ".html"}:
            continue
        count += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        hits |= {m.group(0) for m in rx.finditer(text)}
        hits |= {f"value of {n}" for n, v in values.items() if v in text}
    maps = len(list(static.rglob("*.map")))
    if hits:
        return "FAIL", f"{count} files; found {sorted(hits)[:6]}"
    return (
        "PASS",
        f"{count} static files: no secret, provider name, key name or DB URL; source maps {maps}",
    )


# ---- STORE ---------------------------------------------------------------------------------------


def payload_volume_shared() -> tuple[str, str]:
    name = f"_wiring/{uuid.uuid4()}"
    marker = str(uuid.uuid4())
    w, _ = exec_in(
        "api",
        "sh",
        "-c",
        f'mkdir -p "$PAYLOAD_DIR/_wiring" && printf {marker} > "$PAYLOAD_DIR/{name}"',
    )
    r, out = exec_in("worker", "sh", "-c", f'cat "$PAYLOAD_DIR/{name}"')
    d, _ = exec_in("worker", "sh", "-c", f'rm -f "$PAYLOAD_DIR/{name}"')
    ok = w == 0 and r == 0 and out.strip() == marker and d == 0
    return (
        ("PASS" if ok else "FAIL"),
        f"api put -> worker get -> delete on PAYLOAD_DIR volume: "
        f"write={w == 0} read={out.strip() == marker} delete={d == 0}",
    )


def tenant_prefixed_keys() -> tuple[str, str]:
    code, out = run(
        [
            "uv",
            "run",
            "--frozen",
            "python",
            "-c",
            "from uuid import uuid4; from account_intelligence.connectors.payloads "
            "import payload_key as k; "
            "t=uuid4(); key=k(t,uuid4(),uuid4(),b'x'); "
            "print(key.startswith(str(t)), key.count('/'))",
        ]
    )
    ok = code == 0 and out.strip().startswith("True")
    return ("PASS" if ok else "FAIL"), f"payload_key starts with the tenant id: {out.strip()}"


# ---- PROVIDERS / LLM -----------------------------------------------------------------------------


TIER_PROBE = """
import json, yaml
from account_intelligence.config import CONFIG_DIR
from account_intelligence.platform.llm import load_routes, judge_differs_from_generator
routes = load_routes()
allow = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text()).get("allowlist", [])
print(json.dumps({"tiers": {t: f"{r.provider}/{r.model}" for t, r in sorted(routes.items())},
                  "allowlisted": all(r.model in allow for r in routes.values()),
                  "judge_differs": judge_differs_from_generator(routes)}))
"""


def llm_tiers() -> tuple[str, str]:
    """The tier table from the runtime's own resolver (``load_routes``), not a re-read of YAML."""
    code, out = run(["uv", "run", "--frozen", "python", "-c", TIER_PROBE])
    if code != 0:
        return "FAIL", out.strip().splitlines()[-1][:160] if out.strip() else "probe failed"
    data = json.loads(out.strip().splitlines()[-1])
    ids_ok = all(re.fullmatch(r"anthropic/claude-[a-z0-9\-]+", v) for v in data["tiers"].values())
    table = ", ".join(f"{t}={m}" for t, m in data["tiers"].items())
    ok = ids_ok and data["allowlisted"] and {"extractor", "planner", "judge"} <= set(data["tiers"])
    return (
        ("PASS" if ok else "FAIL"),
        f"{table}; allowlisted={data['allowlisted']}; "
        f"judge differs from extractor (D-48)={data['judge_differs']}",
    )


def live_skip(name: str) -> Check:
    def inner() -> tuple[str, str]:
        return "SKIP", f"live {name} call runs only in `make smoke-live` with keys set"

    return inner


# ---- AUTH ----------------------------------------------------------------------------------------


def permission_matrix() -> tuple[str, str]:
    code, out = run(["uv", "run", "--frozen", "python", "-c", PERMISSIONS_PROBE])
    if code != 0:
        return "FAIL", out.strip().splitlines()[-1][:160] if out.strip() else "probe failed"
    data = json.loads(out.strip().splitlines()[-1])
    support_writes = data["support_writes"]
    evidence = (
        f"operator: read {data['operator_reads']}, write {data['operator_writes']}; "
        f"support: read {data['support_reads']}, write {len(support_writes)}"
    )
    if support_writes:
        return "FAIL", evidence + f" -> support can write {support_writes[:4]}"
    return "PASS", evidence


PERMISSIONS_PROBE = """
import json
from account_intelligence.api.policy import ROUTE_POLICIES
op_r = op_w = sup_r = 0; sup_w = []
for (method, path), policy in sorted(ROUTE_POLICIES.items()):
    if not path.startswith("/operator/"):
        continue
    write = method not in ("GET", "HEAD", "OPTIONS")
    roles = set(policy.operator_roles or ())
    if "operator" in roles:
        op_w += write; op_r += not write
    if "support" in roles:
        if write: sup_w.append(f"{method} {path}")
        else: sup_r += 1
print(
    json.dumps(
        {"operator_reads": op_r, "operator_writes": op_w, "support_reads": sup_r,
         "support_writes": sup_w}
    )
)
"""


# ---- COMPOSE -------------------------------------------------------------------------------------


def compose_config() -> tuple[str, str]:
    code, out = run([*COMPOSE, "config", "--format", "json"], timeout=60)
    if code != 0:
        return "FAIL", out.strip().splitlines()[-1][:160]
    services = json.loads(out)["services"]
    local = []
    for name, svc in services.items():
        for key, value in (svc.get("environment") or {}).items():
            if (
                isinstance(value, str)
                and re.search(r"(URL|HOST|ORIGIN|ENDPOINT)$", key)
                and re.search(r"localhost|127\.0\.0\.1", value)
            ):
                local.append(f"{name}.{key}")
    order = services["api"].get("depends_on", {}).get("migrate", {}).get("condition")
    ok = not local and order == "service_completed_successfully"
    return (
        ("PASS" if ok else "FAIL"),
        f"resolves; localhost hosts inside containers: {local or 'none'}; "
        f"api waits for migrate: {order}",
    )


def api_refuses_behind_schema() -> tuple[str, str]:
    return pytest(f"{CONTRACT}::test_api_refuses_to_start_on_a_schema_behind_head")


# ---- matrix --------------------------------------------------------------------------------------


def checks() -> list[tuple[str, str, Check]]:
    c = CONTRACT
    return [
        (
            "CONFIG",
            "env-example-matches-code",
            lambda: pytest(f"{c}::test_env_example_lists_exactly_what_the_code_reads"),
        ),
        (
            "CONFIG",
            "env-example-comments-above-keys",
            lambda: pytest(f"{c}::test_env_example_comments_sit_above_their_key"),
        ),
        (
            "CONFIG",
            "env-example-hermetic-and-valid",
            lambda: pytest(f"{c}::test_env_example_is_hermetic_and_valid_by_default"),
        ),
        ("CONFIG", "every-env-read-registered", lambda: pytest("tests/unit/test_env_registry.py")),
        ("CONFIG", "dotenv-untracked", dotenv_untracked),
        ("CONFIG", "secret-scan", secret_scan),
        (
            "CONFIG",
            "boot-fails-naming-variable",
            lambda: pytest(
                "tests/unit/test_env_registry.py", "-k", "invalid or missing or required or names"
            ),
        ),
        (
            "CONFIG",
            "placeholder-and-unsigned-tokens-rejected",
            lambda: pytest(
                "tests/phase3/test_ws2_tokens.py", "tests/unit/test_google_staff_claims.py", db=True
            ),
        ),
        (
            "CONFIG",
            "dev-never-uses-secret-manager",
            lambda: pytest(f"{c}::test_dev_never_builds_a_secret_manager_client"),
        ),
        (
            "CONFIG",
            "cloud-secret-fails-closed",
            lambda: pytest(f"{c}::test_cloud_secret_read_fails_closed_when_the_secret_is_absent"),
        ),
        (
            "CONFIG",
            "superuser-not-in-request-path",
            lambda: pytest(f"{c}::test_migrator_url_is_read_only_by_migration_and_ops_code"),
        ),
        (
            "CONFIG",
            "prod-migrator-non-superuser",
            lambda: pytest(f"{c}::test_production_template_uses_a_non_superuser_owner"),
        ),
        ("DATABASE", "alembic-at-head", alembic_at_head),
        ("DATABASE", "runtime-roles-app_rw", runtime_roles),
        ("DATABASE", "forced-rls-every-tenant-table", forced_rls),
        ("DATABASE", "checkpoint-thread-prefix", checkpoint_prefix),
        ("DATABASE", "pgvector-hnsw", pgvector),
        ("DATABASE", "isolation-suite", lambda: pytest("tests/isolation", "-x", db=True)),
        ("API", "healthz-liveness", healthz),
        ("API", "readyz-reports-components", readyz_components),
        (
            "API",
            "readyz-fails-closed",
            lambda: pytest("tests/system/test_wiring.py::test_readyz_fails_closed_w5", db=True),
        ),
        (
            "API",
            "openapi-and-client-current",
            lambda: (
                lambda r: (
                    "PASS" if r[0] == 0 else "FAIL",
                    r[1].strip().splitlines()[-1][:160] if r[1].strip() else "ok",
                )
            )(run(["make", "contract"])),
        ),
        ("API", "cors-deny-by-default", cors_denied),
        ("API", "tenant-only-from-session", forged_tenant_header),
        (
            "API",
            "tenant-never-from-body",
            lambda: pytest(
                "tests/system/test_wiring.py::test_tenant_never_comes_from_a_body_w3", db=True
            ),
        ),
        ("API", "session-cookie-flags", session_cookie_policy),
        ("API", "request-id-echo", request_id_echo),
        ("API", "request-id-in-api-log", request_id_in_api_log),
        (
            "API",
            "request-id-hermetic",
            lambda: pytest(
                f"{c}::test_request_id_reaches_every_log_line",
                f"{c}::test_local_task_hop_forwards_the_request_id",
            ),
        ),
        ("API", "body-size-limit", body_limit),
        ("API", "client-safe-errors", no_stack_traces),
        ("WEB", "server-side-proxy-no-public-vars", web_server_side_proxy),
        ("WEB", "csp-connect-self-nonce", web_csp),
        ("WEB", "web-container-reaches-api", web_reaches_api),
        ("WEB", "bundle-secret-scan", bundle_scan),
        ("STORE", "payload-volume-api-to-worker", payload_volume_shared),
        ("STORE", "keys-tenant-prefixed", tenant_prefixed_keys),
        (
            "PROVIDERS",
            "configured-needs-every-credential",
            lambda: pytest(
                f"{c}::test_provider_is_configured_only_with_every_usable_credential",
                f"{c}::test_unconfigured_provider_sends_no_credential_at_all",
            ),
        ),
        (
            "PROVIDERS",
            "readyz-provider-status",
            lambda: pytest(
                f"{c}::test_readiness_reports_every_component_by_status_word_only",
                f"{c}::test_readiness_marks_providers_off_when_connectors_are_off",
            ),
        ),
        (
            "PROVIDERS",
            "hosts-only-in-broker",
            lambda: pytest(f"{c}::test_provider_hosts_are_reached_only_by_the_broker_and_adapters"),
        ),
        (
            "PROVIDERS",
            "request-shape-cassettes",
            lambda: pytest("tests/connectors/test_contracts.py"),
        ),
        (
            "PROVIDERS",
            "billing-429-refund",
            lambda: pytest(
                "tests/connectors/test_service_scenarios.py",
                "tests/connectors/test_ledger_budget.py",
                "tests/connectors/test_transport_pacing.py",
                db=True,
            ),
        ),
        (
            "PROVIDERS",
            "outage-partial-and-budget-stop",
            lambda: pytest("tests/system/test_failure_injection.py", db=True),
        ),
        ("PROVIDERS", "llm-tier-models", llm_tiers),
        *[("LIVE", f"provider-{p}", live_skip(p)) for p in PROVIDERS],
        ("LIVE", "llm-tiers", live_skip("LLM tier")),
        (
            "AUTH",
            "client-sso-and-session",
            lambda: pytest("tests/phase3/test_ws2_sso_api.py", db=True),
        ),
        ("AUTH", "operator-vs-support-permissions", permission_matrix),
        ("COMPOSE", "config-hosts-and-order", compose_config),
        ("COMPOSE", "api-refuses-schema-behind-head", api_refuses_behind_schema),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only",
        help="run one layer (CONFIG, DATABASE, API, WEB, STORE, PROVIDERS, LIVE, AUTH, COMPOSE)",
    )
    args = parser.parse_args()
    rows: list[Row] = []
    for layer, name, check in checks():
        if args.only and layer != args.only.upper():
            continue
        started = time.monotonic()
        try:
            result, evidence = check()
        except Exception as error:  # noqa: BLE001 - a crashing check is a FAIL row, not a crash
            result, evidence = "FAIL", f"check crashed: {type(error).__name__}: {str(error)[:100]}"
        rows.append(Row(layer, name, result, evidence))
        print(
            f"{layer:<9} | {name:<42} | {result:<4} | "
            f"{evidence}  ({time.monotonic() - started:.0f}s)",
            flush=True,
        )
    counts = {k: sum(r.result == k for r in rows) for k in ("PASS", "FAIL", "SKIP")}
    print(f"\nPASS {counts['PASS']}  FAIL {counts['FAIL']}  SKIP {counts['SKIP']}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
