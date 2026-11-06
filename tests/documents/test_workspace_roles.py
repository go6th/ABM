"""Member roles of a workspace zip from its paths alone (``documents.workspace``): the package
layout decides what is a deliverable, a note, a captured page, a raw pull, a build file or an
owner-only file; a zip without a root marker is a loose upload with no roles. No database."""

from __future__ import annotations

from documents.workspace import (
    DELIVERABLE_ROLES,
    MemberRole,
    marker_missing,
    may_import,
    member_roles,
    skip_check,
    workspace_root,
)

PACKAGE = [
    "ws/",
    "ws/RESTORE.md",
    "ws/BD_Owner_Only.docx",
    "ws/deliverables/BD_Account_Summary.docx",
    "ws/deliverables/BD_Sources_and_Evidence.docx",
    "ws/deliverables/BD_People_List.xlsx",
    "ws/deliverables/BD_Job_Postings.xlsx",
    "ws/deliverables/BD_Tech_Stack_and_Teams.xlsx",
    "ws/deliverables/BD_Executive_Brief.pptx",
    "ws/notes/pull_log_phase1.md",
    "ws/notes/sense_check.md",
    "ws/data/jobs_12mo.json",
    "ws/data/people_grid.txt",
    "ws/data/releases/2026-09-21_arthur-hu-joins.md",
    "ws/build/build_jobs.py",
    "ws/build/jobs_all.json",
    "ws/build/docx_lib.js",
    "ws/README.txt",
]


def test_workspace_member_roles_follow_the_package_layout() -> None:
    """Every member of a package gets the role its folder and name say; the root is the folder
    holding RESTORE.md; the build and owner-only members are the ones the read skips; only the
    deliverable workbooks feed the import."""
    assert workspace_root(PACKAGE) == "ws/"
    roles = member_roles(PACKAGE)
    assert "ws/" not in roles  # folders are not members
    assert roles["ws/RESTORE.md"] is MemberRole.RESTORE
    assert roles["ws/BD_Owner_Only.docx"] is MemberRole.OWNER_ONLY
    assert roles["ws/deliverables/BD_Account_Summary.docx"] is MemberRole.DELIVERABLE_SUMMARY
    assert roles["ws/deliverables/BD_Sources_and_Evidence.docx"] is MemberRole.DELIVERABLE_EVIDENCE
    assert roles["ws/deliverables/BD_People_List.xlsx"] is MemberRole.DELIVERABLE_PEOPLE
    assert roles["ws/deliverables/BD_Job_Postings.xlsx"] is MemberRole.DELIVERABLE_JOBS
    assert roles["ws/deliverables/BD_Tech_Stack_and_Teams.xlsx"] is MemberRole.DELIVERABLE_TECH
    assert roles["ws/deliverables/BD_Executive_Brief.pptx"] is MemberRole.DELIVERABLE_OTHER
    assert roles["ws/notes/pull_log_phase1.md"] is MemberRole.NOTE
    assert roles["ws/data/jobs_12mo.json"] is MemberRole.RAW_PULL
    assert roles["ws/data/people_grid.txt"] is MemberRole.RAW_PULL
    assert roles["ws/data/releases/2026-09-21_arthur-hu-joins.md"] is MemberRole.CAPTURED_PAGE
    assert roles["ws/build/build_jobs.py"] is MemberRole.BUILD
    assert roles["ws/build/jobs_all.json"] is MemberRole.BUILD
    assert roles["ws/README.txt"] is MemberRole.OTHER
    # what the read skips, and what the import may read
    assert skip_check(roles, "ws/build/jobs_all.json") == "build_skipped"
    assert skip_check(roles, "ws/BD_Owner_Only.docx") == "owner_only_skipped"
    assert skip_check(roles, "ws/data/jobs_12mo.json") is None
    assert skip_check(roles, None) is None
    importable = {m for m in roles if may_import(roles, m)}
    assert importable == {m for m, r in roles.items() if r in DELIVERABLE_ROLES}
    assert not may_import(roles, "ws/data/jobs_12mo.json")
    assert not may_import(roles, "ws/build/jobs_all.json")
    assert may_import(roles, "ws/somewhere/else.xlsx") is False


def test_a_zip_without_a_root_marker_has_no_roles_and_imports_everything() -> None:
    """A loose bundle of files keeps the old behaviour: nothing skipped, every table may import;
    a marker deeper than the shallowest wins nothing; a MANIFEST.json also marks a root."""
    loose = ["jobs.xlsx", "notes/a.md", "build/x.py"]
    assert workspace_root(loose) is None
    assert member_roles(loose) == {}
    assert may_import({}, "build/x.py") and skip_check({}, "build/x.py") is None
    nested = ["MANIFEST.json", "a/RESTORE.md", "a/build/x.py", "build/y.py"]
    assert workspace_root(nested) == ""
    roles = member_roles(nested)
    assert roles["build/y.py"] is MemberRole.BUILD
    assert roles["a/build/x.py"] is MemberRole.OTHER  # not under the root's build folder
    assert roles["MANIFEST.json"] is MemberRole.RESTORE
    # an internal folder and the product's own Internal Only file are owner-only too
    inner = member_roles(["ws/RESTORE.md", "ws/internal/notes.docx", "ws/Acme_Internal_Only.docx"])
    assert inner["ws/internal/notes.docx"] is MemberRole.OWNER_ONLY
    assert inner["ws/Acme_Internal_Only.docx"] is MemberRole.OWNER_ONLY


FALLBACK_ROOT = [
    "deliverables/BD_People_List.xlsx",
    "deliverables/BD_Job_Postings.xlsx",
    "notes/pull_log.md",
    "build/build_jobs.py",
    "data/jobs_12mo.json",
]


def test_a_zip_with_deliverables_and_no_marker_is_a_workspace() -> None:
    """The marker fallback (PR5): the parent of a deliverables/ folder holding a deliverable file
    is the root, whether that is the zip's root or one folder down; roles are unchanged."""
    assert workspace_root(FALLBACK_ROOT) == ""
    assert marker_missing(FALLBACK_ROOT)
    roles = member_roles(FALLBACK_ROOT)
    assert roles["deliverables/BD_People_List.xlsx"] is MemberRole.DELIVERABLE_PEOPLE
    assert roles["deliverables/BD_Job_Postings.xlsx"] is MemberRole.DELIVERABLE_JOBS
    assert roles["notes/pull_log.md"] is MemberRole.NOTE
    assert roles["build/build_jobs.py"] is MemberRole.BUILD
    assert roles["data/jobs_12mo.json"] is MemberRole.RAW_PULL
    assert may_import(roles, "deliverables/BD_People_List.xlsx")
    assert not may_import(roles, "data/jobs_12mo.json")

    nested = ["acme/" + n for n in FALLBACK_ROOT] + ["acme/README.txt"]
    assert workspace_root(nested) == "acme/"
    assert marker_missing(nested)
    assert member_roles(nested)["acme/deliverables/BD_People_List.xlsx"] is (
        MemberRole.DELIVERABLE_PEOPLE
    )
    assert member_roles(nested)["acme/README.txt"] is MemberRole.OTHER


def test_the_fallback_needs_a_deliverable_file_and_never_beats_a_marker() -> None:
    # a loose bundle with a deliverables/ folder that holds no deliverable file stays loose
    loose = ["deliverables/readme.txt", "deliverables/notes.docx", "other/data.csv"]
    assert workspace_root(loose) is None and not marker_missing(loose)
    assert member_roles(loose) == {}
    assert workspace_root(["a.csv", "b.xlsx", "c_people_list.xlsx"]) is None
    # a marker decides: no fallback, nothing logged
    with_marker = ["ws/RESTORE.md", *["other/" + n for n in FALLBACK_ROOT]]
    assert workspace_root(with_marker) == "ws/"
    assert not marker_missing(with_marker)
    assert marker_missing(PACKAGE) is False
