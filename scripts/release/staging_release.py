#!/usr/bin/env python3
"""Staging release pipeline: build, preflight, migrate, deploy, verify and roll back, in one tool.

The owner runs it (every step touches the Google Cloud project). DEPLOYMENT.md section 2.2 is the
recipe it automates; the hand-run copies of that recipe lived in session scratch folders and were
lost each time, and a release made while the database was stopped left a revision that could not
start (api 00137, staging 2026-11-07). Commands:

* ``status``: the database state and the revision each service serves.
* ``build --commit C [--patch P ...] [--services api,worker,web]``: one Cloud Build of a snapshot of
  commit ``C`` (``git archive``, never the working tree, so another session's unreleased work is
  not shipped) with the patches applied; writes ``docs/deploy/releases/<tag>.json`` with the
  digests and the snapshot's migration files.
* ``deploy <record> [--allow-migrations] [--dry-run]``: refuses unless the database is RUNNABLE
  with activation policy ALWAYS; refuses a migration the live release does not have unless
  ``--allow-migrations``; runs the migrate job with the new api image (only when the record has
  an api); updates worker, api and web in that order; after each, checks that the new revision
  serves all traffic, that ``/readyz`` answers 200 and ``/v1/models`` 401, and that the revision
  logged no ERROR in its first minute. Any failure moves every service it changed back to its
  previous revision (the database is not rolled back: migrations only go forward). On success the
  digests go into ``infra/envs/staging/terraform.tfvars`` and the record becomes ``live.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "abmgenerator"
REGION = "us-central1"
REGISTRY = f"{REGION}-docker.pkg.dev/{PROJECT}/ai-staging-images"
PUBLIC = "https://136-81-60-29.sslip.io"
API_ORIGIN = "https://ai-staging-api-979669242383.us-central1.run.app"
DB_INSTANCE = "ai-staging-pg"
MIGRATE_JOB = "ai-staging-migrate"
RELEASES = ROOT / "docs/deploy/releases"
TFVARS = ROOT / "infra/envs/staging/terraform.tfvars"
# worker first (it reads what the api writes), the web last (it calls the api)
DEPLOY_ORDER = ("worker", "api", "web")
ERROR_WATCH_S = 60
READY_TRIES = 10

Run = Callable[[Sequence[str]], str]


class ReleaseRefused(RuntimeError):
    """A preflight check failed; nothing was changed."""


def service(name: str) -> str:
    return f"ai-staging-{name}"


def gcloud_run(cmd: Sequence[str]) -> str:
    """Run one command and return its stdout; a non-zero exit raises with its stderr."""
    done = subprocess.run(list(cmd), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {done.stderr.strip()[-800:]}")
    return done.stdout.strip()


# ---- pure parts (tests/unit/test_staging_release.py) --------------------------------------------


def cloudbuild_yaml(tag: str, services: Iterable[str]) -> str:
    """One Cloud Build with a parallel docker step per image (E2_HIGHCPU_8: the larger machine
    is quota-blocked in us-central1)."""
    steps, images = [], []
    for name in services:
        context = "web" if name == "web" else "."
        dockerfile = "web/Dockerfile" if name == "web" else f"docker/{name}.Dockerfile"
        extra = f", --build-arg, API_ORIGIN={API_ORIGIN}" if name == "web" else ""
        ref = f"{REGISTRY}/{name}:{tag}"
        steps.append(
            f"- id: {name}\n  name: gcr.io/cloud-builders/docker\n"
            f"  args: [build, -f, {dockerfile}{extra}, -t, {ref}, {context}]\n"
            "  waitFor: ['-']"
        )
        images.append(f"- {ref}")
    return (
        "options:\n  machineType: E2_HIGHCPU_8\nsteps:\n"
        + "\n".join(steps)
        + "\nimages:\n"
        + "\n".join(images)
        + "\ntimeout: 2400s\n"
    )


def new_migrations(snapshot: Iterable[str], live: Iterable[str]) -> list[str]:
    """The migration files of the snapshot the live release does not have."""
    return sorted(set(snapshot) - set(live))


def tfvars_with(text: str, digests: dict[str, str]) -> str:
    """``terraform.tfvars`` with each released image's digest written in."""
    for name, digest in digests.items():
        text, n = re.subn(
            rf"({re.escape(REGISTRY)}/{name}@sha256:)[0-9a-f]{{64}}",
            rf"\g<1>{digest.removeprefix('sha256:')}",
            text,
        )
        if n != 1:
            raise ReleaseRefused(f"terraform.tfvars has {n} {name} image lines, expected 1")
    return text


def serving_new(traffic_revision: str, latest_created: str, latest_ready: str) -> bool:
    """Whether the revision just created started and serves all traffic."""
    return traffic_revision == latest_created == latest_ready


def healthy(readyz: int, models: int) -> bool:
    """The public checks of DEPLOYMENT.md section 4: ready, and the api refuses an anonymous
    caller (401) rather than failing (5xx) or answering openly (200)."""
    return readyz == 200 and models == 401


# ---- the pipeline -----------------------------------------------------------------------------


@dataclass
class Pipeline:
    run: Run = gcloud_run
    dry_run: bool = False
    log: Callable[[str], None] = print
    changed: dict[str, str] = field(default_factory=dict)  # service -> revision to roll back to

    def act(self, cmd: Sequence[str]) -> str:
        """A command that changes the project: printed, and run unless this is a dry run."""
        self.log("$ " + " ".join(cmd))
        return "" if self.dry_run else self.run(cmd)

    def db_state(self) -> tuple[str, str]:
        out = self.run(
            [
                "gcloud",
                "sql",
                "instances",
                "describe",
                DB_INSTANCE,
                "--project",
                PROJECT,
                "--format",
                "value(state,settings.activationPolicy)",
            ]
        )
        state, _, policy = out.partition("\t")
        return state.strip(), policy.strip()

    def revisions(self, name: str) -> tuple[str, str, str]:
        """(revision serving traffic, latest created, latest ready)."""
        out = self.run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                service(name),
                "--region",
                REGION,
                "--project",
                PROJECT,
                "--format",
                "value(status.traffic[0].revisionName,status.latestCreatedRevisionName,"
                "status.latestReadyRevisionName)",
            ]
        )
        parts = (out.split("\t") + ["", "", ""])[:3]
        return parts[0], parts[1], parts[2]

    def serving_digest(self, name: str) -> str:
        """The image digest of the service's current template (what it serves)."""
        out = self.run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                service(name),
                "--region",
                REGION,
                "--project",
                PROJECT,
                "--format",
                "value(spec.template.spec.containers[0].image)",
            ]
        )
        return out.rpartition("@")[2]

    def http(self, path: str) -> int:
        out = self.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", PUBLIC + path])
        return int(out or 0)

    def errors_since(self, revision: str, since: str) -> int:
        out = self.run(
            [
                "gcloud",
                "logging",
                "read",
                f'resource.type="cloud_run_revision" AND resource.labels.revision_name="{revision}"'
                f' AND severity>=ERROR AND timestamp>="{since}"',
                "--project",
                PROJECT,
                "--limit",
                "20",
                "--format",
                "value(timestamp)",
            ]
        )
        return len([line for line in out.splitlines() if line.strip()])

    def preflight(
        self, record: dict[str, Any], live: dict[str, Any] | None, allow_migrations: bool
    ) -> None:
        state, policy = self.db_state()
        if (state, policy) != ("RUNNABLE", "ALWAYS"):
            raise ReleaseRefused(
                f"the staging database is {state}/{policy}: start it first"
                f" (gcloud sql instances patch {DB_INSTANCE} --activation-policy=ALWAYS)"
            )
        for name, digest in (live or {}).get("digests", {}).items():
            # a release made by hand changed staging under the record (staging 2026-11-08: a
            # second session released HEAD, migrated to 0113, and this release's older image
            # then failed its migrate job): the migration check below would compare against
            # the wrong schema, so stop until the record is taken from what is live
            if (serving := self.serving_digest(name)) != digest:
                raise ReleaseRefused(
                    f"{service(name)} serves {serving[:19]}, live.json says {digest[:19]}:"
                    " staging was released outside this tool. Record what is live first"
                    " (staging_release.py adopt --commit <the commit it was built from>)."
                )
        if "api" in record["digests"] and live is not None:
            added = new_migrations(record["migrations"], live.get("migrations", ()))
            if added and not allow_migrations:
                raise ReleaseRefused(
                    "the release adds migrations the live release does not have: "
                    + ", ".join(added)
                    + ". They cannot be rolled back; pass --allow-migrations to apply them."
                )

    def migrate(self, digest: str) -> None:
        image = f"{REGISTRY}/api@{digest}"
        common = ["--region", REGION, "--project", PROJECT]
        self.act(["gcloud", "run", "jobs", "update", MIGRATE_JOB, *common, "--image", image])
        self.act(["gcloud", "run", "jobs", "execute", MIGRATE_JOB, *common, "--wait"])

    def roll_out(self, name: str, digest: str) -> None:
        before, _, _ = self.revisions(name)
        self.changed[name] = before
        since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        common = ["--region", REGION, "--project", PROJECT]
        self.act(
            [
                "gcloud",
                "run",
                "services",
                "update",
                service(name),
                *common,
                "--image",
                f"{REGISTRY}/{name}@{digest}",
            ]
        )
        if self.dry_run:
            return
        traffic, created, ready = self.revisions(name)
        if created == ready and traffic != created:
            # traffic was pinned to a revision (an earlier rollback): send it to the new one
            self.act(
                [
                    "gcloud",
                    "run",
                    "services",
                    "update-traffic",
                    service(name),
                    *common,
                    "--to-latest",
                ]
            )
            traffic, created, ready = self.revisions(name)
        if not serving_new(traffic, created, ready):
            raise RuntimeError(f"{service(name)}: revision {created} did not start ({ready} ready)")
        # LOOP(release readiness): cap=READY_TRIES checks 6 s apart; exit=healthy or the cap
        for _ in range(READY_TRIES):
            if healthy(self.http("/readyz"), self.http("/v1/models")):
                break
            time.sleep(6)
        else:
            raise RuntimeError(f"{service(name)}: /readyz or /v1/models unhealthy after deploy")
        time.sleep(ERROR_WATCH_S)
        if (n := self.errors_since(created, since)) > 0:
            raise RuntimeError(f"{service(name)}: revision {created} logged {n} ERROR entries")
        self.log(f"ok {service(name)} -> {created}")

    def roll_back(self) -> None:
        for name, revision in reversed(list(self.changed.items())):
            if revision:
                self.act(
                    [
                        "gcloud",
                        "run",
                        "services",
                        "update-traffic",
                        service(name),
                        "--region",
                        REGION,
                        "--project",
                        PROJECT,
                        f"--to-revisions={revision}=100",
                    ]
                )

    def deploy(
        self, record: dict[str, Any], live: dict[str, Any] | None, allow_migrations: bool
    ) -> None:
        self.preflight(record, live, allow_migrations)
        digests = record["digests"]
        if "api" in digests:
            self.migrate(digests["api"])
        try:
            for name in DEPLOY_ORDER:
                if name in digests:
                    self.roll_out(name, digests[name])
        except Exception:
            self.log("release failed: moving traffic back to the previous revisions")
            self.roll_back()
            raise


# ---- build and the command line ---------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    ).stdout


def build(commit: str, patches: Sequence[Path], services: Sequence[str], run: Run) -> Path:
    sha = git("rev-parse", "--short=8", commit).strip()
    tag = f"rel-{sha}-{datetime.now(UTC).strftime('%m%d%H%M')}"
    with tempfile.TemporaryDirectory(prefix="release-") as tmp:
        snap = Path(tmp) / "snap"
        snap.mkdir()
        archive = subprocess.run(
            ["git", "-C", str(ROOT), "archive", commit], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(snap)], input=archive, check=True)
        for patch in patches:
            subprocess.run(
                ["patch", "-p1", "-s", "-d", str(snap), "-i", str(patch.resolve())], check=True
            )
        (Path(tmp) / "cloudbuild.yaml").write_text(cloudbuild_yaml(tag, services))
        (Path(tmp) / "empty.gcloudignore").write_text("")
        migrations = sorted(p.name for p in (snap / "migrations").glob("*.sql"))
        build_id = run(
            [
                "gcloud",
                "builds",
                "submit",
                str(snap),
                "--project",
                PROJECT,
                "--region",
                REGION,
                "--config",
                str(Path(tmp) / "cloudbuild.yaml"),
                "--ignore-file",
                str(Path(tmp) / "empty.gcloudignore"),
                "--format",
                "value(id)",
            ]
        ).splitlines()[-1]
    out = run(
        [
            "gcloud",
            "builds",
            "describe",
            build_id,
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--format",
            "json(results.images)",
        ]
    )
    images = json.loads(out)["results"]["images"]
    digests = {img["name"].split("/")[-1].split(":")[0]: img["digest"] for img in images}
    record = {
        "tag": tag,
        "commit": git("rev-parse", commit).strip(),
        "build": build_id,
        "patches": [p.name for p in patches],
        "migrations": migrations,
        "digests": digests,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    RELEASES.mkdir(parents=True, exist_ok=True)
    for patch in patches:
        if patch.resolve().parent != RELEASES.resolve():
            shutil.copy(patch, RELEASES / f"{tag}.{patch.name}")
    path = RELEASES / f"{tag}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def adopt(pipeline: Pipeline, commit: str, tag: str) -> Path:
    """Write ``live.json`` from what each service serves now and the migrations of ``commit``
    (the commit those images were built from): after a release made outside this tool."""
    migrations = sorted(
        name.rpartition("/")[2]
        for name in git("ls-tree", "--name-only", commit, "migrations/").split()
        if name.endswith(".sql")
    )
    record = {
        "tag": tag or f"adopted-{git('rev-parse', '--short=8', commit).strip()}",
        "commit": git("rev-parse", commit).strip(),
        "patches": [],
        "migrations": migrations,
        "digests": {name: pipeline.serving_digest(name) for name in DEPLOY_ORDER},
        "adopted_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    RELEASES.mkdir(parents=True, exist_ok=True)
    path = RELEASES / "live.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    a = sub.add_parser("adopt")
    a.add_argument("--commit", required=True)
    a.add_argument("--tag", default="")
    b = sub.add_parser("build")
    b.add_argument("--commit", default="HEAD")
    b.add_argument("--patch", action="append", type=Path, default=[])
    b.add_argument("--services", default="api,worker")
    d = sub.add_parser("deploy")
    d.add_argument("record", type=Path)
    d.add_argument("--allow-migrations", action="store_true")
    d.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    pipeline = Pipeline(dry_run=getattr(args, "dry_run", False))
    try:
        if args.command == "status":
            print("database", *pipeline.db_state())
            for name in DEPLOY_ORDER:
                print(service(name), *pipeline.revisions(name))
        elif args.command == "adopt":
            print(adopt(pipeline, args.commit, args.tag))
        elif args.command == "build":
            services = [s for s in args.services.split(",") if s in DEPLOY_ORDER]
            print(build(args.commit, args.patch, services, gcloud_run))
        else:
            record = json.loads(args.record.read_text())
            live_path = RELEASES / "live.json"
            live = json.loads(live_path.read_text()) if live_path.exists() else None
            pipeline.deploy(record, live, args.allow_migrations)
            if not args.dry_run:
                TFVARS.write_text(tfvars_with(TFVARS.read_text(), record["digests"]))
                merged = {
                    **(live or {}),
                    **record,
                    "digests": {**(live or {}).get("digests", {}), **record["digests"]},
                }
                if "api" not in record["digests"] and live is not None:
                    # only the api image runs migrations: the live schema did not change
                    merged["migrations"] = live.get("migrations", [])
                live_path.write_text(json.dumps(merged, indent=2) + "\n")
                print("released", record["tag"])
    except ReleaseRefused as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
