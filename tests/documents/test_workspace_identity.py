"""Which company an uploaded workspace zip is about (``documents.workspace_identity``), pure.
SYNTHETIC members only."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date

from documents import workspace_identity as WI
from documents.workspace import MemberRole, member_roles
from tests.documents import workspace_zip_owner as OWNER

F = OWNER.FOLDER


def _roles(members: dict[str, bytes]) -> dict[str, MemberRole]:
    return member_roles(list(members))


def test_the_company_record_names_the_domain_the_name_and_the_research_date() -> None:
    members = OWNER.raw_members()
    identity = WI.identity_of(members, _roles(members))
    assert identity.domain == OWNER.WORKSPACE_DOMAIN
    assert identity.name == OWNER.ACCOUNT_NAME
    assert identity.research_date == date(2026, 10, 24)
    assert identity.careers_hosts == (OWNER.CAREERS_HOST,)
    assert f"{F}/data/company_record.json" in identity.evidence


def test_the_restore_note_alone_names_the_company_and_the_day() -> None:
    members = {
        f"{F}/RESTORE.md": b"# Acme workspace (acme.example), October 24, 2026\n\nNotes.\n",
        f"{F}/deliverables/Acme_Job_Postings.xlsx": b"x",
    }
    identity = WI.identity_of(members, _roles(members))
    assert (identity.domain, identity.name) == ("acme.example", "Acme")
    assert identity.research_date == date(2026, 10, 24)


def test_a_package_naming_no_company_is_not_held() -> None:
    members = {f"{F}/RESTORE.md": b"# Notes\n", f"{F}/deliverables/A_Job_Postings.xlsx": b"x"}
    identity = WI.identity_of(members, _roles(members))
    assert not identity.found
    assert WI.decide(identity, frozenset({"other.example"})) == "same"


def test_the_decision_compares_registrable_domains_with_the_bound_ones() -> None:
    identity = WI.WorkspaceIdentity(domain="octavia-bio.example", name="Octavia")
    assert WI.decide(identity, WI.account_bound_domains("octavia-us.example", {})) == "mismatch"
    assert WI.decide(identity, WI.account_bound_domains("www.octavia-bio.example", {})) == "same"
    bound = WI.account_bound_domains(
        "octavia-us.example", {"domains": ["jobs.octavia-bio.example"]}
    )
    assert WI.decide(identity, bound) == "same"


def test_binding_keeps_existing_entries_and_adds_the_workspace_once() -> None:
    identity = WI.WorkspaceIdentity(
        domain="octavia-bio.example",
        name="Octavia Bio",
        careers_hosts=("careers.octavia-bio.example",),
        aliases=("Octavia",),
    )
    once = WI.bound_watch_config({"domains": ["old.example"], "aliases": ["Old"]}, identity)
    assert once["domains"] == ["old.example", "octavia-bio.example"]
    assert once["aliases"] == ["Old", "Octavia Bio", "Octavia"]
    assert once["careers_hosts"] == ["careers.octavia-bio.example"]
    assert WI.bound_watch_config(once, identity) == once


def test_only_the_small_identity_members_are_read_from_the_zip() -> None:
    data = OWNER.workspace_zip_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        roles = member_roles(z.namelist())
    members = WI.read_identity_members(data, roles)
    names = sorted(m.rsplit("/", 1)[-1] for m in members)
    assert names == [
        "RESTORE.md",
        "company_record.json",
        "live_postings_index.json",
        "org_resolve.json",
    ]
    assert all(len(v) <= WI.MAX_MEMBER_BYTES for v in members.values())
    assert json.loads(members[f"{F}/data/company_record.json"])["domain"] == OWNER.WORKSPACE_DOMAIN


def test_parse_day_reads_iso_timestamps_and_written_dates() -> None:
    assert WI.parse_day("2026-10-24T08:12:58Z") == date(2026, 10, 24)
    assert WI.parse_day("October 24, 2026") == date(2026, 10, 24)
    assert WI.parse_day("pulled last week") is None
