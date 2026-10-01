"""Infrastructure as code and the one-command deploy (roadmap s6 environments and CI)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENVS = ("dev", "staging", "production")
TF = REPO / "infra" / "terraform"
DEPLOY = REPO / "scripts" / "deploy.sh"


def _tfvars(env: str) -> dict[str, str]:
    text = (TF / "envs" / env / "terraform.tfvars").read_text(encoding="utf-8")
    return dict(re.findall(r"^(\w+)\s*=\s*\"?([^\"\n]+)\"?$", text, re.M))


@pytest.mark.parametrize("env", ENVS)
def test_each_environment_is_defined(env: str) -> None:
    main = (TF / "envs" / env / "main.tf").read_text(encoding="utf-8")
    assert f'environment         = "{env}"' in main
    assert 'source              = "../../modules/environment"' in main
    tfvars = _tfvars(env)
    assert {"cloud_provider", "region"} <= set(tfvars)


def test_environments_share_one_variable_set() -> None:
    keys = [set(_tfvars(env)) for env in ENVS]
    assert keys[0] == keys[1] == keys[2]


def test_production_protections() -> None:
    assert _tfvars("production")["deletion_protection"] == "true"
    module = (TF / "modules" / "environment" / "main.tf").read_text(encoding="utf-8")
    assert "Production needs a chosen cloud_provider" in module


def test_non_production_stays_offline() -> None:
    for env in ("dev", "staging"):
        tfvars = _tfvars(env)
        assert tfvars["model_mode"] == "fake"
        assert tfvars["broker_mode"] == "recorded"


def test_module_secret_names_match_app_settings() -> None:
    from app.settings import LIVE_KEYS

    module = (TF / "modules" / "environment" / "main.tf").read_text(encoding="utf-8")
    block = module[
        module.index("secret_names = [") : module.index("]", module.index("secret_names"))
    ]
    names = set(re.findall(r'"([A-Z_]+)"', block))
    assert set(LIVE_KEYS) | {"DATABASE_URL"} == names


def test_deploy_script_is_strict_and_executable() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert os.access(DEPLOY, os.X_OK)
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert re.search(
        r"^deploy-staging:\n\tTERRAFORM=\$\(TERRAFORM\) scripts/deploy.sh staging", makefile, re.M
    )


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""), **(env or {})}
    return subprocess.run(  # noqa: S603
        [str(DEPLOY), *args], capture_output=True, text=True, env=full_env, check=False, cwd=REPO
    )


def test_deploy_rejects_unknown_environment() -> None:
    result = _run("qa")
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_deploy_requires_terraform() -> None:
    result = _run("staging", env={"TERRAFORM": "/nonexistent/terraform"})
    assert result.returncode == 2
    assert "terraform not found" in result.stderr


def test_real_deploy_requires_secrets_from_environment(tmp_path: Path) -> None:
    fake_tf = tmp_path / "terraform"
    fake_tf.write_text("#!/bin/sh\nexit 0\n")
    fake_tf.chmod(0o755)
    result = _run("staging", env={"TERRAFORM": str(fake_tf), "DRY_RUN": "0"})
    assert result.returncode == 2
    assert ("IMAGE_REGISTRY" in result.stderr) or ("dirty" in result.stderr)


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform binary not installed")
def test_staging_dry_run_plans() -> None:
    result = _run("staging", env={"DRY_RUN": "1", "SKIP_CHECKS": "1"})
    assert result.returncode == 0, result.stderr
    assert "dry run complete" in result.stdout
