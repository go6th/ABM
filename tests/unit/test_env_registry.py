"""The typed environment registry (account_intelligence.config)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from account_intelligence.config import (
    CLOUD,
    EnvironmentInvalid,
    Settings,
    load_settings,
    main,
    registry,
)

ROOT = Path(__file__).resolve().parents[2]
READ = re.compile(
    r"""(?:environ\.get|environ\[|getenv|env\.get|env\[|_require\(env,\s*)\(?["']([A-Z][A-Z0-9_]{2,})["']"""
)
LOCAL_OK = {
    "APP_ENV": "dev",
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
    "CHECKPOINT_DATABASE_URL": "postgresql://u:p@localhost/db",
}
CLOUD_OK = {
    "APP_ENV": "staging",
    "CHECKPOINT_DATABASE_URL": "postgresql://u@/db",
    "CLOUD_SQL_INSTANCE": "p:r:i",
    "DB_IAM_USER": "runtime@p.iam",
    "DB_NAME": "ai",
    "JWKS_URL": "https://idp.test/jwks",
    "JWT_AUDIENCE": "aud",
    "JWT_ISSUER": "https://idp.test/",
    "GCP_PROJECT": "p",
    "GCP_REGION": "us-central1",
    "TASKS_QUEUE": "runs",
    "WORKER_URL": "https://worker.test",
    "TASKS_SERVICE_ACCOUNT": "tasks@p.iam",
    "TASKS_AUDIENCE": "https://worker.test",
    "PAYLOAD_BUCKET": "bucket",
    "REDIS_HOST": "10.0.0.3",
    "PUBLIC_BASE_URL": "https://app.test",
}


def _reads() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        names |= set(READ.findall(path.read_text()))
    providers = yaml.safe_load((ROOT / "config/providers.yaml").read_text())["providers"]
    names |= {p["auth"]["env"] for p in providers.values() if p.get("auth", {}).get("env")}
    return names


def test_every_env_read_is_registered() -> None:
    registered = {row["name"] for row in registry()}
    missing = sorted(_reads() - registered)
    assert not missing, f"unregistered environment variables: {missing}"


def test_every_row_documents_purpose_and_secrecy() -> None:
    for row in registry():
        assert row["purpose"] and isinstance(row["secret"], bool), row["name"]


def test_local_profile_validates() -> None:
    s = load_settings(LOCAL_OK)
    assert s.runtime_database_url.startswith("postgresql+asyncpg://")


def test_cloud_profile_validates_without_a_password_url() -> None:
    s = load_settings(CLOUD_OK)
    assert s.app_env == "staging" and s.runtime_database_url.startswith("postgresql+asyncpg://")


@pytest.mark.parametrize("missing", ["GCP_PROJECT", "CLOUD_SQL_INSTANCE", "PUBLIC_BASE_URL"])
def test_cloud_profile_requires_its_variables(missing: str) -> None:
    env = {k: v for k, v in CLOUD_OK.items() if k != missing}
    with pytest.raises(EnvironmentInvalid, match=missing):
        load_settings(env)


@pytest.mark.parametrize(
    "switch",
    [
        ("ANTHROPIC_API_KEY", "sk-should-not-be-here"),
        ("ALLOW_UNAUTHENTICATED_TASKS", "1"),
        ("PAYLOAD_DIR", "/tmp/x"),
    ],
)
def test_cloud_profile_refuses_local_switches(switch: tuple[str, str]) -> None:
    for profile in CLOUD:
        with pytest.raises(EnvironmentInvalid, match=switch[0]) as info:
            load_settings({**CLOUD_OK, "APP_ENV": profile, switch[0]: switch[1]})
        assert switch[1] not in str(info.value)  # names, never values


def test_errors_never_print_secret_values() -> None:
    with pytest.raises(EnvironmentInvalid) as info:
        load_settings({**LOCAL_OK, "DATABASE_URL": "mysql://user:hunter2@db/x"})
    assert "hunter2" not in str(info.value) and "DATABASE_URL" in str(info.value)


def test_missing_local_database_url_fails() -> None:
    env = {k: v for k, v in LOCAL_OK.items() if k != "DATABASE_URL"}
    with pytest.raises(EnvironmentInvalid, match="DATABASE_URL"):
        load_settings(env)


def test_staff_plane_is_all_or_nothing() -> None:
    with pytest.raises(EnvironmentInvalid, match="go together"):
        load_settings({**LOCAL_OK, "STAFF_JWT_ISSUER": "https://accounts.google.com"})


def test_cloud_urls_must_be_https() -> None:
    with pytest.raises(EnvironmentInvalid, match="WORKER_URL must be https"):
        load_settings({**CLOUD_OK, "WORKER_URL": "http://worker.test"})


def test_vertex_needs_a_region_in_the_cloud() -> None:
    with pytest.raises(EnvironmentInvalid, match="VERTEX_REGION"):
        load_settings({**CLOUD_OK, "LLM_ACCESS": "vertex_ai"})


def test_unknown_profile_and_bad_types_fail() -> None:
    with pytest.raises(EnvironmentInvalid, match="APP_ENV"):
        load_settings({**LOCAL_OK, "APP_ENV": "prod-ish"})
    with pytest.raises(EnvironmentInvalid, match="RUN_BUDGET_USD"):
        load_settings({**LOCAL_OK, "RUN_BUDGET_USD": "-1"})


def test_cli_reports_pass_and_fail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for k, v in LOCAL_OK.items():
        monkeypatch.setenv(k, v)
    assert main() == 0 and "PASS" in capsys.readouterr().out
    monkeypatch.setenv("APP_ENV", "staging")
    assert main() == 1 and "GCP_PROJECT" in capsys.readouterr().err


def test_settings_are_frozen() -> None:
    s = Settings.model_validate({k.lower(): v for k, v in LOCAL_OK.items()})
    with pytest.raises(Exception):  # noqa: B017 - pydantic frozen instance error
        s.app_env = "production"  # type: ignore[misc]


def test_environment_doc_is_current_and_complete() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from gen_env_docs import OUT, names, render

    assert OUT.read_text() == render(), "run python scripts/gen_env_docs.py"
    web = set()
    for path in (ROOT / "web").rglob("*.ts*"):
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        web |= set(re.findall(r"process\.env\.([A-Z_][A-Z0-9_]+)", path.read_text()))
    assert web <= names(), sorted(web - names())
    assert not re.search(r"(?i)(sk-ant|password=|secret=)[^|\s`]", OUT.read_text())  # names only
