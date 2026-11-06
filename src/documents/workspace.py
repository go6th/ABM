"""Member roles of an uploaded research-workspace zip (2026-11-03, workspace import phase 0).

A workspace zip is the package a research delivers or a hand-built one of the same shape: a
root folder holding ``RESTORE.md`` or ``MANIFEST.json``, ``deliverables/`` with the five client
files, ``notes/``, ``data/`` (the raw pulls, ``data/releases/`` the captured pages) and
``build/`` (the scripts and intermediates that turned data into deliverables). Until now every
member was read the same way and the deliverables import matched every table by its column
names, so a ``build/jobs_all.json`` intermediate imported the same postings a second and third
time, the build scripts became searchable prose, and an owner-only note shipped by mistake was
indexed like a client file.

This module decides, from the member paths alone, what each member is (``MemberRole``) and
what the read does with it: a build script or intermediate is listed but not read
(``build_skipped``); an owner-only file is listed but not read (``owner_only_skipped``: upload it
on its own); a raw pull is read into the table store and the passages but never into the
deliverables import; only the deliverable workbooks feed that import. A zip without a workspace
root (a loose bundle of files) keeps the old behaviour: every member is read and every table
may import. Only names are inspected; no member is opened here.

A later package is "90 to 95 percent the same" (owner 2026-11-05), so the names are read with
some give: a deliverable is known by the words of its name as well as by the product's exact
suffix ("UCB People List.xlsx", "Sources & Evidence.docx"), the deliverables folder in any case,
and a package without a marker or a deliverables folder is still a workspace when deliverables
of two kinds sit side by side (a package zipped without its folders). A workbook named in no
known way is registered by what its sheets hold (``workspace_import.sniff_kind``).

GUARANTEE(test_workspace_member_roles_follow_the_package_layout)
GUARANTEE(test_a_workspace_zip_imports_each_deliverable_row_once_and_skips_build_and_owner_files)
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum

ROOT_MARKERS = frozenset({"RESTORE.md", "MANIFEST.json"})


class MemberRole(StrEnum):
    """What a member of a workspace zip is, from its path."""

    DELIVERABLE_SUMMARY = "deliverable_summary"
    DELIVERABLE_EVIDENCE = "deliverable_evidence"
    DELIVERABLE_PEOPLE = "deliverable_people"
    DELIVERABLE_JOBS = "deliverable_jobs"
    DELIVERABLE_TECH = "deliverable_tech"
    DELIVERABLE_OTHER = "deliverable_other"
    OWNER_ONLY = "owner_only"
    NOTE = "note"
    RESTORE = "restore"
    CAPTURED_PAGE = "captured_page"
    RAW_PULL = "raw_pull"
    BUILD = "build"
    OTHER = "other"


DELIVERABLE_ROLES = frozenset(
    {
        MemberRole.DELIVERABLE_SUMMARY,
        MemberRole.DELIVERABLE_EVIDENCE,
        MemberRole.DELIVERABLE_PEOPLE,
        MemberRole.DELIVERABLE_JOBS,
        MemberRole.DELIVERABLE_TECH,
        MemberRole.DELIVERABLE_OTHER,
    }
)
# roles the read lists but never parses, with the skip check the coverage report names
SKIPPED_ROLES: Mapping[MemberRole, str] = {
    MemberRole.BUILD: "build_skipped",
    MemberRole.OWNER_ONLY: "owner_only_skipped",
}
# what each role is called where members are listed (the file map, the uploads card)
ROLE_LABELS: Mapping[str, str] = {
    MemberRole.DELIVERABLE_SUMMARY: "account summary",
    MemberRole.DELIVERABLE_EVIDENCE: "sources and evidence",
    MemberRole.DELIVERABLE_PEOPLE: "people list",
    MemberRole.DELIVERABLE_JOBS: "job postings",
    MemberRole.DELIVERABLE_TECH: "tech stack and teams",
    MemberRole.DELIVERABLE_OTHER: "other deliverable",
    MemberRole.OWNER_ONLY: "owner-only file",
    MemberRole.NOTE: "note",
    MemberRole.RESTORE: "package manifest",
    MemberRole.CAPTURED_PAGE: "captured page",
    MemberRole.RAW_PULL: "raw pull",
    MemberRole.BUILD: "build file",
    MemberRole.OTHER: "other file",
}
SKIP_LABELS = {
    "build_skipped": "a build script or intermediate, not research data",
    "owner_only_skipped": "an owner-only file; upload it on its own",
}

_DELIVERABLE_SUFFIXES = (
    ("_account_summary.docx", MemberRole.DELIVERABLE_SUMMARY),
    ("_sources_and_evidence.docx", MemberRole.DELIVERABLE_EVIDENCE),
    ("_people_list.xlsx", MemberRole.DELIVERABLE_PEOPLE),
    ("_job_postings.xlsx", MemberRole.DELIVERABLE_JOBS),
    ("_tech_stack_and_teams.xlsx", MemberRole.DELIVERABLE_TECH),
)
_OWNER_ONLY = re.compile(r"(_only|_internal_only)\.docx$", re.IGNORECASE)

# The words that name each deliverable when a package names its files in its own way (owner
# 2026-11-05: later packages are "90 to 95 percent the same": "UCB People List.xlsx",
# "UCB - Job Postings v2.xlsx", "Sources & Evidence.docx"). Checked in this order on the file
# name in words (lower case, "&" read as "and", every other sign a space), with the extension
# the deliverable has; the first that fits wins.
_R = MemberRole
_DELIVERABLE_WORDS: tuple[tuple[str, tuple[str, ...], MemberRole], ...] = (
    (".docx", ("sources and evidence", "source register", "evidence"), _R.DELIVERABLE_EVIDENCE),
    (".docx", ("account summary", "account brief", "summary"), _R.DELIVERABLE_SUMMARY),
    (".xlsx", ("people list", "people", "contacts", "buying group"), _R.DELIVERABLE_PEOPLE),
    (".xlsx", ("job postings", "job posting", "postings", "jobs"), _R.DELIVERABLE_JOBS),
    (".xlsx", ("tech stack", "technology", "tech", "teams"), _R.DELIVERABLE_TECH),
)  # fmt: skip
_NON_WORD = re.compile(r"[^a-z0-9]+")
# folders whose files are never deliverables, whatever their names
_NOT_DELIVERABLE_FOLDERS = frozenset({"notes", "build", "data", "internal", "owner"})


def _words(name: str) -> str:
    stem = name.lower().replace("&", " and ")
    return f" {_NON_WORD.sub(' ', stem).strip()} "


def deliverable_role(name: str) -> MemberRole | None:
    """The deliverable a file is by its name: the exact suffix of the product's own packages
    first (``_People_List.xlsx``), else the words of the name (``UCB People List.xlsx``,
    ``Sources & Evidence.docx``); None for any other name."""
    lower = name.lower()
    for suffix, role in _DELIVERABLE_SUFFIXES:
        if lower.endswith(suffix):
            return role
    if _OWNER_ONLY.search(lower):
        return None
    words = _words(posixpath.splitext(lower)[0])
    for ext, phrases, role in _DELIVERABLE_WORDS:
        if lower.endswith(ext) and any(f" {p} " in words for p in phrases):
            return role
    return None


def _parts(path: str) -> list[str]:
    return [p for p in posixpath.normpath(path.replace("\\", "/")).split("/") if p and p != "."]


def _marker_root(names: Iterable[str]) -> str | None:
    """The shallowest folder prefix holding a root marker, else None."""
    best: str | None = None
    for name in names:
        parts = _parts(name)
        if not parts or parts[-1] not in ROOT_MARKERS:
            continue
        prefix = "/".join(parts[:-1])
        prefix = f"{prefix}/" if prefix else ""
        if best is None or prefix.count("/") < best.count("/"):
            best = prefix
    return best


def _deliverables_root(names: Iterable[str]) -> str | None:
    """The fallback root (PR5, 2026-11-04) when no marker is found: the parent of the shallowest
    ``deliverables/`` folder (any case) that holds a deliverable file; else (2026-11-05, a
    package zipped without its folders) the shallowest folder that holds deliverable files of at
    least two kinds side by side. None when there is neither."""
    best: str | None = None
    kinds: dict[str, set[MemberRole]] = {}
    for name in names:
        parts = _parts(name)
        role = deliverable_role(parts[-1]) if parts else None
        if role is None:
            continue
        if any(p.lower() in _NOT_DELIVERABLE_FOLDERS for p in parts[:-1]):
            continue
        if len(parts) >= 2 and parts[-2].lower() == "deliverables":
            prefix = "/".join(parts[:-2])
            prefix = f"{prefix}/" if prefix else ""
            if best is None or prefix.count("/") < best.count("/"):
                best = prefix
            continue
        folder = "/".join(parts[:-1])
        kinds.setdefault(f"{folder}/" if folder else "", set()).add(role)
    if best is not None:
        return best
    side_by_side = [prefix for prefix, found in kinds.items() if len(found) >= 2]
    return min(side_by_side, key=lambda p: (p.count("/"), p)) if side_by_side else None


def workspace_root(names: Iterable[str]) -> str | None:
    """The folder prefix (``""`` for the zip's root, else ``"<folder>/"``) that holds a root
    marker, the shallowest when several do; without a marker, the parent of a ``deliverables/``
    folder that holds a deliverable file (``marker_missing``); None when the zip is not a
    workspace package."""
    names = list(names)
    found = _marker_root(names)
    return found if found is not None else _deliverables_root(names)


def marker_missing(names: Iterable[str]) -> bool:
    """True when the zip is a workspace only by the ``deliverables/`` fallback (no RESTORE.md or
    MANIFEST.json anywhere): the caller logs ``documents.workspace_marker_missing`` once."""
    names = list(names)
    return _marker_root(names) is None and _deliverables_root(names) is not None


def classify_member(path: str, root: str) -> MemberRole:
    """The role of one member under ``root`` (a prefix from ``workspace_root``)."""
    parts = _parts(path)
    root_parts = _parts(root)
    if parts[: len(root_parts)] != root_parts:
        return MemberRole.OTHER
    rel = parts[len(root_parts) :]
    if not rel:
        return MemberRole.OTHER
    name = rel[-1]
    lower = name.lower()
    folder = rel[0].lower() if len(rel) > 1 else ""
    if _OWNER_ONLY.search(lower) or folder in ("internal", "owner"):
        return MemberRole.OWNER_ONLY
    if len(rel) == 1:
        if name in ROOT_MARKERS:
            return MemberRole.RESTORE
        # a package zipped without its folders: the deliverables sit at the root
        return deliverable_role(name) or MemberRole.OTHER
    if folder == "deliverables":
        return deliverable_role(name) or MemberRole.DELIVERABLE_OTHER
    if folder == "notes":
        return MemberRole.NOTE
    if folder == "build":
        return MemberRole.BUILD
    if folder == "data":
        return MemberRole.CAPTURED_PAGE if rel[1].lower() == "releases" else MemberRole.RAW_PULL
    # a deliverable in a folder of another name ("Final/UCB People List.xlsx")
    return deliverable_role(name) or MemberRole.OTHER


def member_roles(names: Iterable[str]) -> dict[str, MemberRole]:
    """Every member's role, or an empty mapping when the zip is not a workspace package (then
    nothing is skipped and every table may import)."""
    listed = [n for n in names if not n.endswith("/")]
    root = workspace_root(listed)
    if root is None:
        return {}
    return {name: classify_member(name, root) for name in listed}


def skip_check(roles: Mapping[str, MemberRole], member: str | None) -> str | None:
    """The skip check for a member the read lists but does not parse, else None."""
    if not member:
        return None
    role = roles.get(member)
    return SKIPPED_ROLES.get(role) if role is not None else None


def may_import(roles: Mapping[str, MemberRole], member: str | None) -> bool:
    """Whether a table from this member feeds the deliverables import: every table of a loose
    upload (no roles), only the deliverable workbooks of a workspace package."""
    if not roles or not member:
        return True
    role = roles.get(member, MemberRole.OTHER)
    if role is MemberRole.DELIVERABLE_OTHER and member.lower().endswith(".docx"):
        # a Word file of a package whose name says neither summary nor evidence: its tables
        # are stored, never read as postings or people (a renamed Account Summary added 64
        # postings and 50 people that way, 2026-11-05)
        return False
    return role in DELIVERABLE_ROLES
