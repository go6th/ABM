"""The three package lists in ``pyproject.toml`` name every top-level package under ``src/``.

A package missing from hatch ``packages`` is not shipped, one missing from import-linter
``root_packages`` is not layered, and one missing from coverage ``source_pkgs`` is never measured
(that is how ``jobs``, ``people``, ``documents``, ``research_files`` and ``devstore`` went
unmeasured until 2026-10-22).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _pyproject() -> dict:  # type: ignore[type-arg]
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _src_packages() -> set[str]:
    return {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}


def test_hatch_importlinter_and_coverage_name_every_src_package() -> None:
    tool = _pyproject()["tool"]
    hatch = {Path(entry).name for entry in tool["hatch"]["build"]["targets"]["wheel"]["packages"]}
    linted = set(tool["importlinter"]["root_packages"])
    covered = set(tool["coverage"]["run"]["source_pkgs"])
    actual = _src_packages()
    assert actual, "no packages under src/"
    for name, listed in (
        ("hatch packages", hatch),
        ("root_packages", linted),
        ("source_pkgs", covered),
    ):
        assert listed == actual, (
            f"{name}: missing {sorted(actual - listed)}, extra {sorted(listed - actual)}"
        )


def test_no_package_is_listed_twice() -> None:
    tool = _pyproject()["tool"]
    for name, entries in (
        ("hatch packages", tool["hatch"]["build"]["targets"]["wheel"]["packages"]),
        ("root_packages", tool["importlinter"]["root_packages"]),
        ("source_pkgs", tool["coverage"]["run"]["source_pkgs"]),
    ):
        assert len(entries) == len(set(entries)), f"{name} repeats an entry"
