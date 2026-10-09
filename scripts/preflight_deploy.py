#!/usr/bin/env python3
"""Validate deployment inputs and Secret Manager wiring without displaying values."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TF_VARS = (
    "TF_VAR_project_id",
    "TF_VAR_github_repository",
    "TF_VAR_image",
    "TF_VAR_db_tier",
)
REQUIRED_SECRET_IDS = {
    "p-search-api-key",
    "p-scrape-api-key",
    "p-signals-api-key",
    "p-signals-api-token",
    "p-people-api-key",
    "p-org-api-key",
    "anthropic-api-key",
    "checkpoint-database-url",
}


def _secret_wiring_errors() -> list[str]:
    """Check that all runtime secret references are declared in the environment stack."""
    tf = (ROOT / "infra/envs/production/main.tf").read_text()
    secrets_block = re.search(r"secrets\s*=\s*\[(.*?)\]", tf, flags=re.S)
    env_block = re.search(r"secret_env\s*=\s*\{(.*?)\n\s*\}", tf, flags=re.S)
    if secrets_block is None or env_block is None:
        return ["production Terraform secret declarations are missing"]
    declared = set(re.findall(r'"([a-z0-9-]+)"', secrets_block.group(1)))
    referenced = set(re.findall(r'=\s*"([a-z0-9-]+)"', env_block.group(1)))
    errors: list[str] = []
    for name in sorted(REQUIRED_SECRET_IDS - declared):
        errors.append(f"required Secret Manager resource is not declared: {name}")
    for name in sorted(referenced - declared):
        errors.append(f"runtime secret reference has no declared resource: {name}")
    for name in sorted(declared - REQUIRED_SECRET_IDS):
        errors.append(f"declared secret is not part of the reviewed required set: {name}")
    return errors


def _get_tf_var(name: str) -> str:
    """Read Terraform's case-sensitive TF_VAR name (which mirrors lowercase variable names)."""
    return os.environ.get(name, "").strip()


def check(env_name: str) -> list[str]:
    """Return missing or invalid deployment inputs, never their supplied values."""
    if env_name not in {"staging", "production"}:
        return ["environment must be staging or production"]
    errors: list[str] = []
    for key in REQUIRED_TF_VARS:
        value = os.environ.get(key, "").strip()
        if not value:
            errors.append(f"missing {key}")
        elif any(marker in value.upper() for marker in ("REPLACE", "YOUR_", "<", ">")):
            errors.append(f"{key} still contains a placeholder")
    bucket = os.environ.get("TF_STATE_BUCKET", "").strip()
    if not bucket:
        errors.append("missing TF_STATE_BUCKET")
    elif not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,220}[a-z0-9]", bucket):
        errors.append("TF_STATE_BUCKET does not look like a bucket name")
    image = _get_tf_var("TF_VAR_image")
    project_id = _get_tf_var("TF_VAR_project_id")
    if project_id and not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id):
        errors.append("TF_VAR_project_id does not match the Google Cloud project ID format")
    if image and not re.search(r"@sha256:[0-9a-fA-F]{64}$", image):
        errors.append("TF_VAR_image must end with an immutable 64-character sha256 digest")
    repository = _get_tf_var("TF_VAR_github_repository")
    if repository and not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        errors.append("TF_VAR_github_repository must have OWNER/REPOSITORY form")
    errors.extend(_secret_wiring_errors())
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, choices=("staging", "production"))
    args = parser.parse_args()
    errors = check(args.env)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        print(f"Preflight failed for {args.env}; values were not displayed.")
        return 1
    print(
        f"Preflight passed for {args.env}; required Terraform inputs and secret references "
        "are present."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
