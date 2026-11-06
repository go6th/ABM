"""The local stacks never listen beyond this machine, and the images build reproducibly (fix
prompt D, task 6; ADR-0121).

* Every port compose publishes binds 127.0.0.1: the dev Postgres, Redis and fake object store run
  with fixed passwords (or none), so ``0.0.0.0`` exposed them to the whole network.
* No Dockerfile runs ``apt-get upgrade``: an image is defined by its pinned base digest; a fixable
  CVE in the base is answered by bumping the digest, not by an unpinned upgrade at build time.
* Every Python base image is pinned by digest."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["compose.yml", "compose.replica.yml"]
DOCKERFILES = sorted((ROOT / "docker").glob("*.Dockerfile")) + [ROOT / "web" / "Dockerfile"]


@pytest.mark.parametrize("name", COMPOSE)
def test_every_published_port_binds_the_loopback_address(name: str) -> None:
    doc = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    published = [
        (service, str(port))
        for service, spec in doc["services"].items()
        for port in spec.get("ports", [])
    ]
    assert published, name
    open_ports = [(s, p) for s, p in published if not p.startswith("127.0.0.1:")]
    assert not open_ports, f"{name}: bind these to 127.0.0.1: {open_ports}"


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.name)
def test_no_image_upgrades_packages_at_build_time(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"apt-get\s+(-y\s+)?(dist-)?upgrade", text), path.name


@pytest.mark.parametrize(
    "path", sorted((ROOT / "docker").glob("*.Dockerfile")), ids=lambda p: p.name
)
def test_python_base_images_are_pinned_by_digest(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FROM python:"):
            assert "@sha256:" in line, (path.name, line)
