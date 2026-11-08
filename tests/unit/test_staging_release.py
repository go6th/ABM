"""The staging release pipeline (``scripts/release/staging_release.py``) without the cloud: a fake
command runner stands in for gcloud and curl. Staging 2026-11-07: a release while the database was
stopped left an api revision that could not start."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts/release/staging_release.py"
_spec = importlib.util.spec_from_file_location("staging_release", _PATH)
assert _spec is not None and _spec.loader is not None
rel: Any = importlib.util.module_from_spec(_spec)
sys.modules["staging_release"] = rel
_spec.loader.exec_module(rel)

DIGEST = "sha256:" + "a" * 64
OLD = "sha256:" + "b" * 64


class Cloud:
    """Answers the pipeline's read commands and records the ones that change the project."""

    def __init__(self, *, db: str = "RUNNABLE\tALWAYS", starts: bool = True, errors: int = 0):
        self.db, self.starts, self.errors = db, starts, errors
        self.created: dict[str, int] = {"worker": 118, "api": 136, "web": 87}
        self.serving = dict(self.created)
        self.changes: list[str] = []
        self.images: dict[str, str] = {}

    def __call__(self, cmd: Sequence[str]) -> str:
        line = " ".join(cmd)
        if line.startswith("gcloud sql instances describe"):
            return self.db
        if line.startswith("gcloud run services describe") and "containers[0].image" in line:
            name = cmd[4].removeprefix("ai-staging-")
            return f"{rel.REGISTRY}/{name}@{self.images.get(name, OLD)}"
        if line.startswith("gcloud run services describe"):
            name = cmd[4].removeprefix("ai-staging-")
            new = f"{name}-{self.created[name]}"
            ready = new if self.starts else f"{name}-{self.serving[name]}"
            return f"{name}-{self.serving[name]}\t{new}\t{ready}"
        if line.startswith("curl"):
            return "401" if cmd[-1].endswith("/v1/models") else "200"
        if line.startswith("gcloud logging read"):
            return "\n".join("2026-11-08T00:00:00Z" for _ in range(self.errors))
        self.changes.append(line)
        if line.startswith("gcloud run services update ai-staging-"):
            name = cmd[4].removeprefix("ai-staging-")
            self.created[name] += 1
            if self.starts:
                self.serving[name] = self.created[name]
        return ""


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rel.time, "sleep", lambda _s: None)


def _record(**digests: str) -> dict[str, Any]:
    return {"tag": "rel-x", "digests": digests, "migrations": ["0111_a.sql"]}


def test_a_stopped_database_refuses_the_release_before_anything_changes() -> None:
    cloud = Cloud(db="STOPPED\tNEVER")
    with pytest.raises(rel.ReleaseRefused, match="STOPPED/NEVER"):
        rel.Pipeline(run=cloud, log=lambda _m: None).deploy(_record(api=DIGEST), None, False)
    assert cloud.changes == []


def test_a_new_migration_needs_the_explicit_flag() -> None:
    cloud = Cloud()
    record = _record(api=DIGEST) | {"migrations": ["0111_a.sql", "0112_drop.sql"]}
    live = {"migrations": ["0111_a.sql"]}
    with pytest.raises(rel.ReleaseRefused, match="0112_drop.sql"):
        rel.Pipeline(run=cloud, log=lambda _m: None).deploy(record, live, False)
    assert cloud.changes == []
    rel.Pipeline(run=cloud, log=lambda _m: None).deploy(record, live, True)
    assert any("jobs execute ai-staging-migrate" in c for c in cloud.changes)


def test_services_roll_out_worker_first_after_the_migrations() -> None:
    cloud = Cloud()
    rel.Pipeline(run=cloud, log=lambda _m: None).deploy(
        _record(web=DIGEST, api=DIGEST, worker=DIGEST), {"migrations": ["0111_a.sql"]}, False
    )
    order = [c.split()[0:5] for c in cloud.changes]
    assert [o[1:4] for o in order] == [
        ["run", "jobs", "update"],
        ["run", "jobs", "execute"],
        ["run", "services", "update"],
        ["run", "services", "update"],
        ["run", "services", "update"],
    ]
    assert [c.split()[4] for c in cloud.changes[2:]] == [
        "ai-staging-worker",
        "ai-staging-api",
        "ai-staging-web",
    ]


def test_a_revision_that_does_not_start_rolls_traffic_back() -> None:
    cloud = Cloud(starts=False)
    with pytest.raises(RuntimeError, match="did not start"):
        rel.Pipeline(run=cloud, log=lambda _m: None).deploy(_record(worker=DIGEST), None, False)
    assert cloud.changes[-1].endswith("--to-revisions=worker-118=100")


def test_errors_in_the_first_minute_roll_back_every_service_changed() -> None:
    cloud = Cloud(errors=2)
    with pytest.raises(RuntimeError, match="2 ERROR"):
        rel.Pipeline(run=cloud, log=lambda _m: None).deploy(
            _record(worker=DIGEST, api=DIGEST), None, False
        )
    rollbacks = [c for c in cloud.changes if "update-traffic" in c]
    assert rollbacks == [
        "gcloud run services update-traffic ai-staging-worker --region us-central1"
        " --project abmgenerator --to-revisions=worker-118=100"
    ]


def test_a_dry_run_changes_nothing() -> None:
    cloud = Cloud()
    lines: list[str] = []
    rel.Pipeline(run=cloud, dry_run=True, log=lines.append).deploy(_record(api=DIGEST), None, False)
    assert cloud.changes == []
    assert any("services update ai-staging-api" in line for line in lines)


def test_tfvars_get_the_new_digest_and_only_one_line_each() -> None:
    text = f'  api    = "{rel.REGISTRY}/api@{OLD}"\n  worker = "{rel.REGISTRY}/worker@{OLD}"\n'
    out = rel.tfvars_with(text, {"api": DIGEST})
    assert f"api@{DIGEST}" in out and f"worker@{OLD}" in out
    with pytest.raises(rel.ReleaseRefused):
        rel.tfvars_with(text, {"web": DIGEST})


def test_the_build_runs_each_image_in_parallel_and_the_web_knows_the_api() -> None:
    yaml = rel.cloudbuild_yaml("rel-t", ["api", "web"])
    assert yaml.count("waitFor: ['-']") == 2
    assert f"API_ORIGIN={rel.API_ORIGIN}" in yaml and "web/Dockerfile" in yaml
    assert "E2_HIGHCPU_8" in yaml


def test_a_release_made_outside_the_tool_is_refused_until_it_is_recorded() -> None:
    """Staging 2026-11-08: a second session released HEAD (schema 0113) by hand; this tool's
    older image then failed its migrate job. The drift is now caught before anything runs."""
    cloud = Cloud()
    cloud.images = {"api": DIGEST, "worker": OLD, "web": OLD}
    live = {"migrations": ["0111_a.sql"], "digests": {"api": OLD, "worker": OLD, "web": OLD}}
    with pytest.raises(rel.ReleaseRefused, match="released outside this tool"):
        rel.Pipeline(run=cloud, log=lambda _m: None).deploy(_record(api=DIGEST), live, False)
    assert cloud.changes == []
    cloud.images["api"] = OLD
    rel.Pipeline(run=cloud, log=lambda _m: None).deploy(_record(api=DIGEST), live, False)
    assert any("services update ai-staging-api" in c for c in cloud.changes)
