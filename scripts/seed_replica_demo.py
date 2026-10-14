"""Seed the DEVELOPMENT demo workspace that matches the Claude Design prototype's sample content.

Client "Meridian Health Partners" with user Priya Nair and six accounts (Highmark, Cambia Health,
AmeriHealth, Cigna, Medica, Availity), each with lanes, findings, people, job postings,
technology, news, signals, sources, a one-minute narrative, a schedule and credit use this
month; a second client, "Northline Group", that holds its own Cigna account to prove isolation;
and five 6th Library accounts published to Meridian.

Everything is synthetic: domains end in ``.example``, people are written as ``[Name]`` exactly
as the design shows them, and source URLs point at the synthetic domains. Refuses unless
``APP_ENV`` is ``dev``, ``local`` or ``test``. Writes through the migrator connection (the
database owner), so it never runs against production credentials.

Usage::

    APP_ENV=dev MIGRATOR_DATABASE_URL=postgresql://postgres:dev@localhost:5432/ai_replica \\
    DEMO_PASSWORD=... uv run python scripts/seed_replica_demo.py [--reset]

``DEMO_PASSWORD`` sets the password of every demo sign-in. Pass it in the shell environment for
the one command; it is not a runtime setting, so it is not in ``.env.example`` and never committed.
``--reset`` removes the demo clients' rows first, so the seed can be run again.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

ALLOWED_ENVS = frozenset({"dev", "local", "test"})
MERIDIAN = "Meridian Health Partners"
NORTHLINE = "Northline Group"
GRADING_HASH = hashlib.sha256(b"replica-demo-grading").hexdigest()


def h(*parts: object) -> str:
    """A stable 64-hex digest of the parts."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


@dataclass
class Finding:
    """One graded fact that the account page and chat cite."""

    key: str
    text: str
    grade: str
    lane: str | None
    source_title: str
    source_path: str
    days_ago: int
    run: str = "first"
    key_finding: bool = False
    news: bool = False
    question: str | None = None


@dataclass
class Account:
    """One demo account and everything it holds."""

    name: str
    domain: str
    lanes: list[str]
    cadence: str
    scans: list[int]
    narrative: str
    findings: list[Finding] = field(default_factory=list)
    people: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    jobs: list[tuple[str, str, int, str]] = field(default_factory=list)
    tech: list[str] = field(default_factory=list)
    signals: list[tuple[str, str, str, int, str]] = field(default_factory=list)
    credits: list[tuple[str, str, int, int]] = field(default_factory=list)
    extra_sources: int = 0
    narrative_keys: list[str] = field(default_factory=list)
    mail_threads: int = 0


def highmark() -> Account:
    """The detailed account of the design (refreshed, two new signals)."""
    acct = Account(
        name="Highmark",
        domain="highmark.example",
        lanes=["Salesforce", "Data and AI"],
        cadence="weekly",
        scans=[10, 2],
        narrative=(
            "Highmark is building out its Salesforce and data platform teams. Open roles point to a"
            " customer data program, and the ServiceNow lane is quiet. The strongest door-opener is"
            " the new integration architect posting."
        ),
    )
    acct.findings = [
        Finding(
            "leadership.technology_leader",
            "New technology leader named in the data organization",
            "confirmed",
            "data_and_ai",
            "Leadership announcement",
            "news/technology-leader",
            6,
            run="latest",
            key_finding=True,
        ),
        Finding(
            "hiring.salesforce_build_roles",
            "Two open Salesforce build roles point to a customer data program",
            "probable",
            "salesforce",
            "Highmark careers posting",
            "careers/salesforce-architect",
            3,
            run="latest",
            key_finding=True,
        ),
        Finding(
            "lane.servicenow_activity",
            "No ServiceNow activity found. Treated as unknown, not absent.",
            "hypothesis",
            "servicenow",
            "Highmark careers search",
            "careers/search-servicenow",
            2,
            run="latest",
            key_finding=True,
            question="Who owns IT service management tooling today?",
        ),
        Finding(
            "hiring.integration_architect",
            "Integration Architect role posted in the platform team",
            "confirmed",
            "salesforce",
            "Highmark careers posting",
            "careers/integration-architect",
            5,
            run="latest",
        ),
        Finding(
            "hiring.data_engineering",
            "Data engineering team hiring for a cloud data platform",
            "probable",
            "data_and_ai",
            "Highmark careers posting",
            "careers/data-engineer",
            12,
        ),
        Finding(
            "tech.salesforce_service_cloud",
            "Salesforce Service Cloud in use for member service",
            "confirmed",
            "salesforce",
            "Customer story",
            "news/service-cloud-story",
            40,
        ),
    ]
    news = [
        "Highmark names a new technology leader for its data organization",
        "Highmark expands member service operations in Pittsburgh",
        "Highmark announces a partnership on value-based care analytics",
        "Highmark reports growth in commercial membership",
        "Highmark opens a new innovation office",
        "Highmark joins an interoperability network",
        "Highmark leaders speak at a healthcare data conference",
        "Highmark updates its digital member experience",
        "Highmark launches a care navigation program",
        "Highmark invests in cloud infrastructure modernization",
        "Highmark publishes its community health report",
        "Highmark adds a chief digital officer role",
    ]
    for i, title in enumerate(news):
        acct.findings.append(
            Finding(
                f"news.item_{i + 1:02d}",
                title,
                "confirmed" if i % 3 else "probable",
                None,
                title,
                f"news/item-{i + 1:02d}",
                3 + i * 4,
                news=True,
            )
        )
    roles = [
        ("SVP, Data and AI Platforms", "Technology", "A", "confirmed", "first"),
        ("VP, Enterprise Architecture", "Technology", "A", "confirmed", "first"),
        ("Head of Customer Data Programs", "Technology", "A", "probable", "first"),
        ("VP, Integration Services", "Technology", "A", "probable", "first"),
        ("Chief Financial Officer", "Finance", "B", "confirmed", "first"),
        ("Senior Director, Sales Operations", "Commercial", "B", "hypothesis", "first"),
        ("Director, Integration Services", "Technology", "B", "probable", "first"),
        ("Director, Digital Experience", "Commercial", "B", "probable", "first"),
        ("Director, Procurement", "Operations", "C", "confirmed", "first"),
        ("Senior Director, Member Services", "Operations", "C", "hypothesis", "first"),
        ("Director, Data Governance", "Technology", "C", "probable", "first"),
        ("Head of Vendor Management", "Operations", "C", "hypothesis", "first"),
    ]
    more_tech = [
        "Director, Cloud Engineering",
        "Director, Analytics Engineering",
        "Director, Platform Reliability",
        "Senior Director, Data Products",
        "Director, Enterprise Applications",
        "Director, Identity and Access",
        "Senior Director, Infrastructure",
        "Director, Machine Learning",
        "Director, Application Development",
        "Director, Technology Strategy",
    ]
    more_comm = [
        "VP, Sales",
        "Director, Broker Relations",
        "Senior Director, Marketing",
        "Director, Product Marketing",
        "VP, Employer Markets",
        "Director, Account Management",
        "Director, Partner Programs",
        "Director, Pricing",
        "Director, Sales Enablement",
    ]
    more_other = [
        "VP, Clinical Operations",
        "Director, Finance Systems",
        "Director, Compliance",
        "Director, Facilities",
        "Director, Talent Acquisition",
        "VP, Legal",
        "Director, Risk",
    ]
    for t in more_tech:
        roles.append((t, "Technology", "B" if "Senior" in t else "C", "probable", "first"))
    for t in more_comm:
        roles.append((t, "Commercial", "A" if t.startswith("VP") else "B", "probable", "first"))
    for t in more_other:
        roles.append(
            (t, "Operations" if "Director" in t else "Finance", "C", "hypothesis", "first")
        )
    roles += [
        ("Chief Technology Officer", "Technology", "A", "confirmed", "first"),
        ("Chief Medical Officer", "Operations", "A", "probable", "first"),
        ("Director, Clinical Analytics", "Operations", "C", "probable", "first"),
    ]
    # Two people first seen in the latest refresh: "Changed this month".
    roles[2] = (roles[2][0], roles[2][1], roles[2][2], roles[2][3], "latest")
    roles[20] = (roles[20][0], roles[20][1], roles[20][2], roles[20][3], "latest")
    acct.people = roles
    acct.jobs = [
        ("Salesforce Architect", "Salesforce", 3, "latest"),
        ("Integration Architect", "Salesforce", 5, "latest"),
        ("Salesforce Developer", "Salesforce", 14, "first"),
        ("Data Engineer", "Data and AI", 12, "first"),
        ("Senior Data Engineer", "Data and AI", 16, "first"),
        ("Machine Learning Engineer", "Data and AI", 18, "first"),
    ]
    filler = [
        "Business Analyst",
        "Product Owner",
        "Cloud Engineer",
        "Scrum Master",
        "QA Analyst",
        "Solutions Architect",
        "Data Analyst",
        "Platform Engineer",
        "Security Analyst",
        "DevOps Engineer",
        "UX Designer",
        "Program Manager",
        "Technical Writer",
        "Site Reliability Engineer",
        "Analytics Manager",
        "Data Scientist",
        "Release Manager",
        "Integration Developer",
        "Database Administrator",
        "Application Support Analyst",
        "Network Engineer",
    ]
    for i, t in enumerate(filler):
        acct.jobs.append((t, "Data and AI" if "Data" in t else "Other", 20 + i, "first"))
    acct.tech = [
        "Salesforce Service Cloud",
        "Salesforce Data Cloud",
        "MuleSoft",
        "Snowflake",
        "Databricks",
        "Tableau",
        "Workday",
        "Okta",
        "AWS",
        "Azure",
        "Epic",
        "Informatica",
        "Power BI",
        "Jira",
    ]
    acct.signals = [
        ("hiring", "Salesforce Architect role posted", "probable", 3, "latest"),
        (
            "leadership",
            "New technology leader named in the data organization",
            "confirmed",
            6,
            "latest",
        ),
        ("hiring", "Data engineering team expanding", "probable", 12, "first"),
        ("hiring", "Machine learning engineer role posted", "probable", 18, "first"),
        ("news", "Partnership on value-based care analytics", "confirmed", 11, "first"),
        ("news", "Cloud infrastructure modernization", "confirmed", 39, "first"),
        ("technology", "Salesforce Data Cloud detected", "probable", 20, "first"),
        ("speaking", "Leaders speak at a healthcare data conference", "confirmed", 27, "first"),
        ("hiring", "Integration architect role posted", "confirmed", 5, "first"),
    ]
    acct.credits = [("scheduled_refresh", "full", 120, 2)]
    acct.extra_sources = 86
    acct.narrative_keys = [
        "hiring.data_engineering",
        "hiring.salesforce_build_roles",
        "hiring.integration_architect",
    ]
    acct.mail_threads = 3
    return acct


def cambia() -> Account:
    acct = Account(
        name="Cambia Health",
        domain="cambiahealth.example",
        lanes=["Salesforce", "ServiceNow"],
        cadence="2w",
        scans=[18, 4],
        narrative=(
            "Cambia Health is adding Salesforce development capacity and has a quiet ServiceNow"
            " lane."
            " The new Salesforce developer posting is the clearest opening for a conversation."
        ),
    )
    acct.findings = [
        Finding(
            "hiring.salesforce_developer",
            "Salesforce Developer role posted",
            "confirmed",
            "salesforce",
            "Cambia Health careers posting",
            "careers/salesforce-developer",
            11,
            run="latest",
            key_finding=True,
        ),
        Finding(
            "tech.servicenow",
            "ServiceNow used for IT service management",
            "probable",
            "servicenow",
            "Cambia Health IT update",
            "news/it-update",
            30,
            key_finding=True,
        ),
        Finding(
            "people.platform_owner",
            "Platform owner for CRM sits in member experience",
            "hypothesis",
            "salesforce",
            "Conference speaker listing",
            "news/speaker",
            25,
            key_finding=True,
            question="Who decides on CRM platform changes?",
        ),
    ]
    acct.people = [
        ("VP, Member Experience", "Commercial", "A", "probable", "first"),
        ("Director, CRM Platforms", "Technology", "A", "confirmed", "first"),
        ("Director, IT Service Management", "Technology", "B", "probable", "first"),
        ("Chief Information Officer", "Technology", "A", "confirmed", "first"),
    ]
    acct.jobs = [
        ("Salesforce Developer", "Salesforce", 11, "latest"),
        ("ServiceNow Administrator", "ServiceNow", 26, "first"),
    ]
    acct.tech = ["Salesforce", "ServiceNow", "Snowflake"]
    acct.signals = [
        ("hiring", "Salesforce Developer role posted", "confirmed", 11, "latest"),
        ("technology", "ServiceNow used for IT service management", "probable", 30, "first"),
    ]
    acct.credits = [("scheduled_refresh", "full", 90, 4)]
    acct.mail_threads = 8
    return acct


def amerihealth() -> Account:
    acct = Account(
        name="AmeriHealth",
        domain="amerihealth.example",
        lanes=["Data and AI"],
        cadence="3d",
        scans=[1],
        narrative=(
            "AmeriHealth is hiring in data engineering this month. The roles lean toward building a"
            " modern data platform rather than analytics reporting."
        ),
    )
    acct.findings = [
        Finding(
            "hiring.data_ai_roles",
            "Data and AI roles posted this month",
            "confirmed",
            "data_and_ai",
            "AmeriHealth careers posting",
            "careers/data-platform-engineer",
            4,
            key_finding=True,
        ),
        Finding(
            "hiring.data_engineering_focus",
            "Hiring leans toward data engineering",
            "probable",
            "data_and_ai",
            "AmeriHealth careers posting",
            "careers/data-engineering-lead",
            4,
            key_finding=True,
        ),
    ]
    acct.people = [
        ("VP, Data Platforms", "Technology", "A", "probable", "first"),
        ("Director, Data Engineering", "Technology", "B", "probable", "first"),
    ]
    acct.jobs = [
        ("Data Platform Engineer", "Data and AI", 4, "first"),
        ("Data Engineering Lead", "Data and AI", 4, "first"),
    ]
    acct.tech = ["Azure", "Databricks"]
    acct.signals = [("hiring", "Data and AI roles posted", "confirmed", 4, "first")]
    acct.credits = [("new_research", "full", 180, 1)]
    return acct


def cigna() -> Account:
    acct = Account(
        name="Cigna",
        domain="cigna.example",
        lanes=["ServiceNow", "SAP"],
        cadence="weekly",
        scans=[6],
        narrative=(
            "Cigna's ServiceNow ownership is not confirmed. Two senior people sit closest to it,"
            " and"
            " the SAP lane shows steady finance systems hiring."
        ),
    )
    acct.findings = [
        Finding(
            "people.servicenow_owner",
            "[Name], VP IT Service Management, is closest to ServiceNow",
            "probable",
            "servicenow",
            "Cigna leadership page",
            "leadership",
            8,
            key_finding=True,
        ),
        Finding(
            "people.enterprise_platforms",
            "[Name], Director of Enterprise Platforms, may own ServiceNow",
            "hypothesis",
            "servicenow",
            "Conference speaker listing",
            "news/speaker-listing",
            16,
            key_finding=True,
            question="Does the enterprise platforms team own ServiceNow?",
        ),
        Finding(
            "hiring.sap_finance",
            "SAP finance systems roles posted",
            "confirmed",
            "sap",
            "Cigna careers posting",
            "careers/sap-analyst",
            9,
            key_finding=True,
        ),
    ]
    acct.people = [
        ("VP, IT Service Management", "Technology", "A", "probable", "first"),
        ("Director, Enterprise Platforms", "Technology", "A", "hypothesis", "first"),
        ("Director, Finance Systems", "Finance", "B", "confirmed", "first"),
    ]
    acct.jobs = [("SAP Finance Analyst", "SAP", 9, "first")]
    acct.tech = ["ServiceNow", "SAP S/4HANA"]
    acct.signals = [
        ("leadership", "IT service management leadership identified", "probable", 8, "first")
    ]
    acct.credits = [("new_research", "full", 170, 6)]
    acct.mail_threads = 7
    return acct


def medica() -> Account:
    acct = Account(
        name="Medica",
        domain="medica.example",
        lanes=["Salesforce"],
        cadence="off",
        scans=[25, 9],
        narrative=(
            "Medica uses Salesforce for broker management. The latest refresh found no material"
            " change."
        ),
    )
    acct.findings = [
        Finding(
            "tech.salesforce_broker",
            "Salesforce used for broker management",
            "probable",
            "salesforce",
            "Medica broker portal",
            "brokers",
            27,
            key_finding=True,
        ),
    ]
    acct.people = [("Director, Broker Technology", "Technology", "B", "probable", "first")]
    acct.tech = ["Salesforce"]
    acct.credits = [("web_refresh", "jobs", 40, 9)]
    return acct


def availity() -> Account:
    acct = Account(
        name="Availity",
        domain="availity.example",
        lanes=["Data and AI", "Tableau"],
        cadence="off",
        scans=[9],
        narrative=(
            "Availity is hiring for analytics platform ownership. Tableau appears across its"
            " reporting"
            " roles."
        ),
    )
    acct.findings = [
        Finding(
            "hiring.analytics_platform",
            "Analytics platform roles posted this month",
            "confirmed",
            "data_and_ai",
            "Availity careers posting",
            "careers/analytics-platform-owner",
            7,
            key_finding=True,
        ),
        Finding(
            "tech.tableau",
            "Tableau used across reporting roles",
            "probable",
            "tableau",
            "Availity careers posting",
            "careers/reporting-analyst",
            9,
            key_finding=True,
        ),
    ]
    acct.people = [("VP, Analytics", "Technology", "A", "probable", "first")]
    acct.jobs = [
        ("Analytics Platform Owner", "Data and AI", 7, "first"),
        ("Reporting Analyst", "Tableau", 9, "first"),
    ]
    acct.tech = ["Tableau", "Snowflake"]
    acct.signals = [("hiring", "Analytics platform roles posted", "confirmed", 7, "first")]
    acct.credits = [("new_research", "full", 160, 9)]
    return acct


LIBRARY = [
    ("Vizient", "vizient.example"),
    ("IQVIA", "iqvia.example"),
    ("GE HealthCare", "gehealthcare.example"),
    ("Thermo Fisher", "thermofisher.example"),
    ("Abbott", "abbott.example"),
]

CADENCE_DAYS = {"3d": 3, "weekly": 7, "2w": 14, "monthly": 30}


class Seeder:
    """Writes one demo client through a single owner connection."""

    def __init__(self, conn: psycopg.Connection[Any], now: datetime) -> None:
        self.conn = conn
        self.now = now
        self.source_seq = 0

    def x(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        return self.conn.execute(sql, params or {})

    def tenant(self, name: str) -> uuid.UUID:
        tid = uuid.uuid4()
        self.x("INSERT INTO tenants (id, name) VALUES (%(id)s, %(n)s)", {"id": tid, "n": name})
        self.x("INSERT INTO wallets (tenant_id, balance) VALUES (%(t)s, 0)", {"t": tid})
        return tid

    def client_settings(self, tid: uuid.UUID, pool: int, cap: int) -> None:
        self.x(
            "INSERT INTO client_settings (tenant_id, account_cap_per_user, idle_expiry_days,"
            " monthly_credit_pool,"
            " fastest_schedule, updated_by) VALUES (%(t)s, %(c)s, 7, %(p)s, '3d', 'seed')",
            {"t": tid, "c": cap, "p": pool},
        )

    def member(
        self,
        tid: uuid.UUID,
        subject: str,
        email: str,
        display: str,
        password_hash: str,
        active_days: int,
    ) -> None:
        self.x(
            "INSERT INTO users (tenant_id, subject, email, display_name) VALUES (%(t)s, %(s)s,"
            " %(e)s, %(d)s)",
            {"t": tid, "s": subject, "e": email, "d": display},
        )
        self.x(
            "INSERT INTO memberships (tenant_id, user_id, role, status, last_active_at)"
            " VALUES (%(t)s, %(s)s, 'analyst', 'active', %(a)s)",
            {"t": tid, "s": subject, "a": self.now - timedelta(days=active_days)},
        )
        found = self.x(
            "SELECT id FROM local_identities WHERE email = %(e)s", {"e": email}
        ).fetchone()
        if found is None:
            self.x(
                "INSERT INTO local_identities (email, password_hash, status, email_verified_at,"
                " password_changed_at,"
                " created_at, updated_at) VALUES (%(e)s, %(p)s, 'active', %(n)s, %(n)s, %(n)s,"
                " %(n)s)",
                {"e": email, "p": password_hash, "n": self.now},
            )
        else:
            self.x(
                "UPDATE local_identities SET password_hash = %(p)s, status = 'active',"
                " email_verified_at = %(n)s,"
                " updated_at = %(n)s WHERE email = %(e)s",
                {"e": email, "p": password_hash, "n": self.now},
            )

    def run(self, tid: uuid.UUID, aid: uuid.UUID, days_ago: int) -> uuid.UUID:
        rid = uuid.uuid4()
        at = self.now - timedelta(days=days_ago)
        self.x(
            "INSERT INTO account_runs (id, tenant_id, account_id, status, grading_config_hash,"
            " created_at, finished_at,"
            " finished_reason, input_mode, run_mode, cap_required, graph_version, budget_json,"
            " reserved_credits,"
            " idempotency_key) VALUES (%(id)s, %(t)s, %(a)s, 'COMPLETED', %(g)s, %(c)s, %(f)s,"
            " 'completed', 'company',"
            " 'full', false, 'replica-demo', '{}', 0, %(k)s)",
            {
                "id": rid,
                "t": tid,
                "a": aid,
                "g": GRADING_HASH,
                "c": at - timedelta(hours=1),
                "f": at,
                "k": f"demo-{rid}",
            },
        )
        self.x(
            "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
            " credit_cap, started_at, finished_at, created_at) VALUES (%(id)s, %(t)s, %(a)s,"
            " 'full', 'completed', 'replica-demo', 1, %(c)s, %(f)s, %(c)s)",
            {"id": rid, "t": tid, "a": aid, "c": at - timedelta(hours=1), "f": at},
        )
        self.tracker(tid, rid, at)
        return rid

    # The demo tracker: most items covered, two partial, the optional items not planned and one
    # source that failed (Blocked with its reason), as a real finished run reports them.
    DEMO_PARTIAL = ("job_postings_12_months", "executive_speaking")
    DEMO_BLOCKED = "regulatory_quality_events"

    def tracker(self, tid: uuid.UUID, rid: uuid.UUID, at: datetime) -> None:
        from account_intelligence.tracker.config import load_checklist

        for row in load_checklist().rows:
            if row.key == self.DEMO_BLOCKED:
                status, reason = "blocked", "source_unavailable"
            elif row.key in self.DEMO_PARTIAL:
                status, reason = "partial", "fewer_than_expected"
            elif row.priority == "optional":
                status, reason = "not_done", "not_planned"
            else:
                status, reason = "done", None
            self.x(
                "INSERT INTO coverage_rows (run_id, row_key, tenant_id, priority, status,"
                " reason_code, proof, owner_stage, last_checked_at) VALUES (%(r)s, %(k)s, %(t)s,"
                " %(p)s, %(s)s, %(why)s, %(proof)s, 'demo', %(at)s)",
                {
                    "r": rid,
                    "k": row.key,
                    "t": tid,
                    "p": row.priority,
                    "s": status,
                    "why": reason,
                    "proof": json.dumps({"calls": [], "notes": ["demo"], "call_refs": []}),
                    "at": at,
                },
            )

    def source(
        self,
        tid: uuid.UUID,
        aid: uuid.UUID,
        run: uuid.UUID,
        url: str,
        title: str,
        publisher: str,
        published: date,
    ) -> tuple[uuid.UUID, str]:
        self.source_seq += 1
        code = f"S-{self.source_seq:04d}"
        sid = uuid.uuid4()
        self.x(
            "INSERT INTO sources (id, tenant_id, account_id, source_code, url, url_normalized,"
            " url_hash, title, publisher,"
            " published_at, kind, first_seen_account_run_id, last_seen_account_run_id)"
            " VALUES (%(id)s, %(t)s, %(a)s, %(c)s, %(u)s, %(u)s, encode(sha256(convert_to(%(u)s,"
            " 'UTF8')), 'hex'),"
            " %(ti)s, %(p)s, %(d)s, 'primary', %(r)s, %(r)s)",
            {
                "id": sid,
                "t": tid,
                "a": aid,
                "c": code,
                "u": url,
                "ti": title,
                "p": publisher,
                "d": published,
                "r": run,
            },
        )
        return sid, code

    def account(self, tid: uuid.UUID, subject: str, acct: Account) -> uuid.UUID:
        aid = uuid.uuid4()
        last = min(acct.scans)
        cadence_days = CADENCE_DAYS.get(acct.cadence)
        next_at = (
            self.now - timedelta(days=last) + timedelta(days=cadence_days) if cadence_days else None
        )
        self.x(
            "INSERT INTO accounts (id, tenant_id, domain, name, primary_lane, lanes,"
            " refresh_cadence, next_refresh_at,"
            " added_by, origin, created_at, updated_at) VALUES (%(id)s, %(t)s, %(d)s, %(n)s,"
            " %(pl)s, %(l)s, %(c)s,"
            " %(nx)s, %(by)s, 'research', %(at)s, %(at)s)",
            {
                "id": aid,
                "t": tid,
                "d": acct.domain,
                "n": acct.name,
                "pl": acct.lanes[0],
                "l": acct.lanes,
                "c": acct.cadence,
                "nx": next_at,
                "by": subject,
                "at": self.now - timedelta(days=max(acct.scans) + 1),
            },
        )
        runs = {"first": self.run(tid, aid, max(acct.scans))}
        runs["latest"] = (
            self.run(tid, aid, min(acct.scans)) if len(acct.scans) > 1 else runs["first"]
        )
        base = f"https://{acct.domain}"
        codes: list[str] = []
        code_of: dict[str, str] = {}
        fact_ids: dict[str, uuid.UUID] = {}
        for f in acct.findings:
            when = self.now - timedelta(days=f.days_ago)
            run = runs[f.run]
            sid, code = self.source(
                tid,
                aid,
                run,
                f"{base}/{f.source_path}",
                f.source_title,
                acct.name if not f.news else f"{acct.name} newsroom",
                when.date(),
            )
            codes.append(code)
            code_of[f.key] = code
            fid = uuid.uuid4()
            flags = ["key_finding"] if f.key_finding else []
            self.x(
                "INSERT INTO facts (id, tenant_id, account_id, attribute_key, value_json,"
                " value_text, fingerprint,"
                " engine_grade, product_grade, read_in_primary_this_run, may_state_to_prospect,"
                " claim_kind, lane,"
                " confirming_question, flags, run_id, created_at) VALUES (%(id)s, %(t)s, %(a)s,"
                " %(k)s, %(vj)s, %(v)s,"
                " %(fp)s, %(eg)s, %(g)s, %(rp)s, %(ms)s, 'sourced', %(l)s, %(q)s, %(fl)s, %(r)s,"
                " %(at)s)",
                {
                    "id": fid,
                    "t": tid,
                    "a": aid,
                    "k": f.key,
                    "vj": json.dumps({"text": f.text}),
                    "v": f.text,
                    "fp": h(acct.domain, f.key),
                    "eg": {"confirmed": "A", "probable": "B"}.get(f.grade, "C"),
                    "g": f.grade,
                    "rp": f.grade == "confirmed",
                    "ms": f.grade == "confirmed",
                    "l": f.lane,
                    "q": f.question,
                    "fl": json.dumps(flags),
                    "r": run,
                    "at": when,
                },
            )
            self.x(
                "INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind, url,"
                " supporting_sentence, span_sha256,"
                " source_id, source_ref, origin, source_tier, date_kind, evidence_date,"
                " provider_code)"
                " VALUES (%(t)s, %(f)s, 'source', %(u)s, %(s)s, %(sp)s, %(sid)s, %(code)s,"
                " 'full_page_read', 'T1',"
                " 'published', %(d)s, 'p_scrape')",
                {
                    "t": tid,
                    "f": fid,
                    "u": f"{base}/{f.source_path}",
                    "s": f.text,
                    "sp": h(f.text),
                    "sid": sid,
                    "code": code,
                    "d": when,
                },
            )
            fact_ids[f.key] = fid
        # Further sources (pages read during research) up to the design's count.
        while len(codes) < acct.extra_sources:
            n = len(codes) + 1
            _, code = self.source(
                tid,
                aid,
                runs["first"],
                f"{base}/page/{n:03d}",
                f"{acct.name} page {n}",
                acct.name,
                (self.now - timedelta(days=10 + n % 30)).date(),
            )
            codes.append(code)
        self.x(
            "INSERT INTO report_sections (tenant_id, account_id, run_id, version, kind, position,"
            " text,"
            " cited_source_codes) VALUES (%(t)s, %(a)s, %(r)s, 1, 'narrative', 0, %(x)s, %(c)s)",
            {
                "t": tid,
                "a": aid,
                "r": runs["latest"],
                "x": acct.narrative,
                "c": [code_of[k] for k in acct.narrative_keys] or codes[:3],
            },
        )
        for i, (title, area, prio, grade, run_key) in enumerate(acct.people):
            run = runs[run_key]
            seen = (
                (self.now - timedelta(days=min(acct.scans)))
                if run_key == "latest"
                else (self.now - timedelta(days=max(acct.scans) + (i % 4)))
            )
            self.x(
                "INSERT INTO people (tenant_id, account_id, run_id, profile_url, name, title,"
                " job_level, source_ref,"
                " scope_status, priority, classification_json, created_at, person_key)"
                " VALUES (%(t)s, %(a)s, %(r)s, %(u)s, '[Name]', %(ti)s, %(lv)s, %(sr)s,"
                " 'in_scope', %(p)s, %(cj)s,"
                " %(at)s, %(pk)s)",
                {
                    "t": tid,
                    "a": aid,
                    "r": run,
                    "u": f"{base}/people/{i + 1:03d}",
                    "ti": title,
                    "lv": "vp"
                    if title.startswith(("VP", "SVP"))
                    else ("c_level" if title.startswith("Chief") else "director"),
                    "sr": codes[i % len(codes)],
                    "p": prio,
                    "cj": json.dumps({"area": area, "grade": grade}),
                    "at": seen,
                    "pk": h(acct.domain, "person", i),
                },
            )
        for i, (title, lane, days, run_key) in enumerate(acct.jobs):
            posted = (self.now - timedelta(days=days)).date()
            url = f"{base}/careers/{title.lower().replace(' ', '-').replace(',', '')}-{i + 1}"
            self.x(
                "INSERT INTO job_postings (tenant_id, account_id, run_id, posting_key, row_json,"
                " status, window_bucket,"
                " url, title, location, posted_at, first_seen, lanes, created_at) VALUES (%(t)s,"
                " %(a)s, %(r)s, %(k)s,"
                " %(rj)s, 'open', 'in_window', %(u)s, %(ti)s, %(lo)s, %(p)s, %(p)s, %(l)s, %(at)s)",
                {
                    "t": tid,
                    "a": aid,
                    "r": runs[run_key],
                    "k": h(acct.domain, "job", i),
                    "rj": json.dumps({"title": title}),
                    "u": url,
                    "ti": title,
                    "lo": ["Pittsburgh, PA", "Remote, US", "Philadelphia, PA", "Portland, OR"][
                        i % 4
                    ],
                    "p": posted,
                    "l": [lane],
                    "at": self.now - timedelta(days=days),
                },
            )
        for i, tech in enumerate(acct.tech):
            self.x(
                "INSERT INTO tech_detections (tenant_id, account_id, run_id, technology,"
                " first_seen_at, last_seen_at,"
                " source_count, source_ref, cross_check_result) VALUES (%(t)s, %(a)s, %(r)s,"
                " %(x)s, %(f)s, %(l)s,"
                " %(n)s, %(s)s, %(c)s)",
                {
                    "t": tid,
                    "a": aid,
                    "r": runs["first"],
                    "x": tech,
                    "f": "2025",
                    "l": "2026",
                    "n": 1 + i % 5,
                    "s": codes[i % len(codes)],
                    "c": "confirmed" if i % 3 else "not_found",
                },
            )
        any_fact = next(iter(fact_ids.values()))
        for i, (kind, title, grade, days, run_key) in enumerate(acct.signals):
            self.x(
                "INSERT INTO signal_events (tenant_id, account_id, fingerprint, signal_type,"
                " event_type, entity, title,"
                " bucket, grade, lead, sensitive, fact_id, score, components, config_version,"
                " detected_at)"
                " VALUES (%(t)s, %(a)s, %(fp)s, %(k)s, 'new', %(e)s, %(ti)s, 1, %(g)s, false,"
                " false, %(f)s, %(s)s,"
                " '[]', 'replica-demo', %(at)s)",
                {
                    "t": tid,
                    "a": aid,
                    "fp": h(acct.domain, "signal", i),
                    "k": kind,
                    "e": acct.name,
                    "ti": title,
                    "g": grade,
                    "f": any_fact,
                    "s": 80 - i * 5,
                    "at": self.now - timedelta(days=days)
                    if run_key == "latest"
                    else self.now - timedelta(days=max(days, max(acct.scans))),
                },
            )
        for purpose, scope, credits, days in acct.credits:
            self.job(tid, aid, subject, purpose, scope, credits, days, runs["latest"])
        self.files(tid, aid, runs["latest"], acct)
        if acct.mail_threads:
            self.mail(tid, aid, subject, acct)
        # The first scan of a refreshed account happened earlier; record it as a done job too.
        if len(acct.scans) > 1:
            self.job(
                tid,
                aid,
                subject,
                "new_research",
                "full",
                0,
                max(acct.scans),
                runs["first"],
                ledger=False,
            )
        return aid

    def files(self, tid: uuid.UUID, aid: uuid.UUID, run: uuid.UUID, acct: Account) -> None:
        """Real Word and Excel deliverables built from the seeded rows, stored as the API does."""
        from account_intelligence.platform.hermetic import LocalPayloadStore

        root = os.environ.get("PAYLOAD_DIR")
        if not root:
            return
        store = LocalPayloadStore(root)
        stem = re.sub(r"[^A-Za-z0-9]+", "-", acct.name).strip("-")
        built = {
            "summary": (f"{stem}-Account-Summary.docx", self.summary_docx(aid, acct)),
            "people": (f"{stem}-People-List.xlsx", self.sheet(aid, "people")),
            "jobs": (f"{stem}-Job-Postings.xlsx", self.sheet(aid, "jobs")),
            "tech": (f"{stem}-Tech-Stack-and-Teams.xlsx", self.sheet(aid, "tech")),
            "evidence": (f"{stem}-Evidence.docx", self.evidence_docx(aid, acct)),
        }
        set_hash = h(acct.domain, "set", *(body for _, body in built.values()))
        for kind, (filename, body) in built.items():
            sha = hashlib.sha256(body).hexdigest()
            key = f"{tid}/{aid}/{run}/deliverables/{sha}.{filename.rsplit('.', 1)[1]}"
            asyncio.run(store.put(key, body))
            self.x(
                "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
                " filename, sha256,"
                " size_bytes, object_key, manifest_json, content_hash, snapshot_hash, theme_hash,"
                " rules_hash, built_at)"
                " VALUES (%(t)s, %(a)s, %(r)s, %(sh)s, %(k)s, 1, %(fn)s, %(sha)s, %(n)s, %(key)s,"
                " '{}', %(sha)s,"
                " %(sh)s, %(sh)s, %(sh)s, %(at)s)",
                {
                    "t": tid,
                    "a": aid,
                    "r": run,
                    "sh": set_hash,
                    "k": kind,
                    "fn": filename,
                    "sha": sha,
                    "n": len(body),
                    "key": key,
                    "at": self.now - timedelta(days=min(acct.scans)),
                },
            )

    def summary_docx(self, aid: uuid.UUID, acct: Account) -> bytes:
        from docx import Document

        doc = Document()
        doc.add_heading(f"{acct.name}: account summary", 0)
        doc.add_heading("The one minute version", 1)
        doc.add_paragraph(acct.narrative)
        doc.add_heading("Key findings", 1)
        table = doc.add_table(rows=1, cols=4)
        for cell, label in zip(
            table.rows[0].cells, ("Grade", "Finding", "Date", "Source"), strict=True
        ):
            cell.text = label
        for grade, value, when, url in self.x(
            "SELECT f.product_grade, f.value_text, e.evidence_date, e.url FROM facts f JOIN"
            " fact_evidence e ON"
            " e.fact_id = f.id WHERE f.account_id = %(a)s AND f.attribute_key NOT LIKE 'news.%%'"
            " ORDER BY e.evidence_date DESC",
            {"a": aid},
        ).fetchall():
            row = table.add_row().cells
            row[0].text, row[1].text, row[2].text, row[3].text = (
                grade.capitalize(),
                value,
                f"{when:%d %b %Y}",
                url,
            )
        doc.add_paragraph("Only Confirmed facts should be said to a prospect.")
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    def evidence_docx(self, aid: uuid.UUID, acct: Account) -> bytes:
        from docx import Document

        doc = Document()
        doc.add_heading(f"{acct.name}: evidence", 0)
        for grade, value, sentence, when, url in self.x(
            "SELECT f.product_grade, f.value_text, e.supporting_sentence, e.evidence_date, e.url"
            " FROM facts f"
            " JOIN fact_evidence e ON e.fact_id = f.id WHERE f.account_id = %(a)s ORDER BY"
            " e.evidence_date DESC",
            {"a": aid},
        ).fetchall():
            doc.add_paragraph(
                f"{value} ({grade.capitalize()}, {when:%d %b %Y})", style="List Bullet"
            )
            doc.add_paragraph(f'Source: {url}. Relied on: "{sentence}"')
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    def sheet(self, aid: uuid.UUID, kind: str) -> bytes:
        from openpyxl import Workbook

        queries = {
            "people": (
                ["Name", "Title", "Area", "Priority", "Fact grade", "Last seen", "Profile"],
                "SELECT coalesce(name, '[Name]'), title, classification_json->>'area', priority,"
                " initcap(classification_json->>'grade'), to_char(created_at, 'DD Mon YYYY'),"
                " profile_url"
                " FROM people WHERE account_id = %(a)s ORDER BY priority, title",
            ),
            "jobs": (
                ["Title", "Lanes", "Location", "Posted", "Link"],
                "SELECT title, array_to_string(lanes, ', '), location, to_char(posted_at, 'DD Mon"
                " YYYY'), url"
                " FROM job_postings WHERE account_id = %(a)s ORDER BY posted_at DESC",
            ),
            "tech": (
                ["Technology", "Sources", "Cross check", "First seen", "Last seen"],
                "SELECT technology, source_count, coalesce(cross_check_result, ''), first_seen_at,"
                " last_seen_at"
                " FROM tech_detections WHERE account_id = %(a)s ORDER BY technology",
            ),
        }
        header, sql = queries[kind]
        book = Workbook()
        sheet = book.active
        sheet.title = "Sheet 1"
        sheet.append(header)
        for row in self.x(sql, {"a": aid}).fetchall():
            sheet.append(list(row))
        out = io.BytesIO()
        book.save(out)
        return out.getvalue()

    def mail(self, tid: uuid.UUID, aid: uuid.UUID, subject: str, acct: Account) -> None:
        """The user's connected mail and the threads matched to this account (private to them)."""
        conn_id = self.x(
            "SELECT id FROM connector_connections WHERE tenant_id = %(t)s AND owner_user_id ="
            " %(u)s AND kind = 'gmail'",
            {"t": tid, "u": subject},
        ).fetchone()
        if conn_id is None:
            conn_id = self.x(
                "INSERT INTO connector_connections (tenant_id, kind, status, scopes, consented_by,"
                " consented_at,"
                " created_by, created_at, owner_user_id, provider, last_synced_at) VALUES (%(t)s,"
                " 'gmail', 'active',"
                " %(sc)s, %(u)s, %(n)s, %(u)s, %(n)s, %(u)s, 'google', %(syn)s) RETURNING id",
                {
                    "t": tid,
                    "u": subject,
                    "n": self.now - timedelta(days=20),
                    "sc": ["https://www.googleapis.com/auth/gmail.readonly"],
                    "syn": self.now.replace(hour=9, minute=12, second=0, microsecond=0),
                },
            ).fetchone()
        topics = [
            "Intro call follow-up",
            "Proposal timing",
            "Integration workstream notes",
            "Budget review dates",
            "Partner question",
            "Security questionnaire",
            "Pilot scope",
            "Reference request",
        ]
        for i in range(acct.mail_threads):
            rid = f"thread-{acct.domain}-{i + 1}"
            topic = topics[i % len(topics)]
            self.x(
                "INSERT INTO connector_items (tenant_id, connection_id, resource_id, sha256,"
                " object_key, bytes, state,"
                " fetched_at, owner_user_id, account_id, item_kind, title, snippet, item_date,"
                " location)"
                " VALUES (%(t)s, %(c)s, %(r)s, %(sha)s, %(k)s, 2048, 'matched', %(n)s, %(u)s,"
                " %(a)s, 'thread', %(ti)s,"
                " %(sn)s, %(d)s, 'Gmail')",
                {
                    "t": tid,
                    "c": conn_id[0],
                    "r": rid,
                    "sha": h(rid),
                    "k": f"tenants/{tid}/connector-quarantine/{h(rid)}",
                    "n": self.now,
                    "u": subject,
                    "a": aid,
                    "ti": f"{acct.name}: {topic}",
                    "sn": f"Thread with the {acct.name} team about {topic.lower()}.",
                    "d": self.now - timedelta(days=2 + i * 3),
                },
            )

    def job(
        self,
        tid: uuid.UUID,
        aid: uuid.UUID,
        subject: str,
        purpose: str,
        scope: str,
        credits: int,
        days: int,
        run: uuid.UUID,
        ledger: bool = True,
    ) -> None:
        jid = uuid.uuid4()
        at = self.now - timedelta(days=days)
        period = at.strftime("%Y-%m")
        self.x(
            "INSERT INTO research_jobs (id, tenant_id, account_id, kind, scope, status, period,"
            " credits_estimated,"
            " credits_used, run_id, requested_by, created_at, started_at, finished_at) VALUES"
            " (%(id)s, %(t)s, %(a)s,"
            " %(k)s, %(s)s, 'done', %(p)s, %(c)s, %(c)s, %(r)s, %(by)s, %(at)s, %(at)s, %(at)s)",
            {
                "id": jid,
                "t": tid,
                "a": aid,
                "k": purpose,
                "s": scope,
                "p": period,
                "c": credits,
                "r": run,
                "by": subject if purpose != "scheduled_refresh" else "scheduler",
                "at": at,
            },
        )
        if not ledger or credits == 0:
            return
        for kind in ("reserve", "settle", "release"):
            self.x(
                "INSERT INTO credit_entries (tenant_id, period, kind, credits, job_id, purpose,"
                " actor, at)"
                " VALUES (%(t)s, %(p)s, %(k)s, %(c)s, %(j)s, %(pu)s, %(by)s, %(at)s)",
                {
                    "t": tid,
                    "p": period,
                    "k": kind,
                    "c": credits,
                    "j": jid,
                    "pu": purpose,
                    "by": subject,
                    "at": at,
                },
            )

    def library(self, publish_to: uuid.UUID) -> None:
        upload = uuid.uuid4()
        self.x(
            "INSERT INTO library_uploads (id, filename, size_bytes, sha256, object_key,"
            " uploaded_by)"
            " VALUES (%(id)s, 'seed-library.zip', 0, %(s)s, 'library/seed-library.zip', 'seed')",
            {"id": upload, "s": h("seed-library")},
        )
        for name, domain in LIBRARY:
            lid = uuid.uuid4()
            content = {
                "name": name,
                "domain": domain,
                "lanes": ["Data and AI"],
                "narrative": f"{name} is investing in data platforms and digital operations.",
                "findings": [
                    {
                        "text": f"{name} is hiring for data platform roles",
                        "grade": "confirmed",
                        "url": f"https://{domain}/careers/data-platform",
                        "lane": "data_and_ai",
                        "days_ago": 12,
                    }
                ],
                "people": [
                    {
                        "title": "VP, Data Platforms",
                        "area": "Technology",
                        "priority": "A",
                        "grade": "probable",
                    }
                ],
                "jobs": [
                    {"title": "Data Platform Engineer", "lane": "Data and AI", "days_ago": 12}
                ],
                "tech": ["Snowflake"],
            }
            self.x(
                "INSERT INTO library_accounts (id, upload_id, name, domain, deliverables_found,"
                " internal_left_out,"
                " check_passed, content) VALUES (%(id)s, %(u)s, %(n)s, %(d)s, 5, true, true,"
                " %(c)s)",
                {"id": lid, "u": upload, "n": name, "d": domain, "c": json.dumps(content)},
            )
            self.x(
                "INSERT INTO library_publications (tenant_id, library_account_id, published_by)"
                " VALUES (%(t)s, %(l)s, 'seed')",
                {"t": publish_to, "l": lid},
            )


DEMO_TABLES = [
    "connector_items",
    "connector_connections",
    "notifications",
    "credit_entries",
    "research_jobs",
    "grade_events",
    "question_categories",
    "library_publications",
    "chat_turns",
    "chat_threads",
    "signal_events",
    "tech_detections",
    "job_postings",
    "people",
    "report_sections",
    "fact_evidence",
    "facts",
    "sources",
    "deliverables",
    "coverage_rows",
    "runs",
    "account_runs",
    "accounts",
    "client_settings",
    "memberships",
    "users",
    "tenant_settings_versions",
    "wallets",
]


def reset(conn: psycopg.Connection[Any]) -> None:
    """Remove the demo clients' rows (only those two tenants) and the seeded library. Append-only
    guards (sources, facts history) are triggers, so they are paused for this dev-only reset."""
    conn.execute("SET LOCAL session_replication_role = replica")
    rows = conn.execute(
        "SELECT id FROM tenants WHERE name IN (%s, %s)", (MERIDIAN, NORTHLINE)
    ).fetchall()
    for (tid,) in rows:
        for table in DEMO_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tid,))  # noqa: S608 fixed table names
        conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))
    conn.execute(
        "DELETE FROM library_accounts WHERE upload_id IN (SELECT id FROM library_uploads WHERE"
        " uploaded_by = 'seed')"
    )
    conn.execute("DELETE FROM library_uploads WHERE uploaded_by = 'seed'")
    conn.execute("SET LOCAL session_replication_role = origin")


RECENT_CHATS = [
    # (sidebar title, question, minutes ago): the design's Recent list, newest first.
    (
        "Salesforce roles this month",
        "Which of my accounts posted Salesforce build roles this month, and who should I call"
        " first?",
        20,
    ),
    ("First call prep: Highmark", "First call questions for Highmark", 90),
    ("Who owns ServiceNow", "Who owns ServiceNow at Cigna?", 300),
]


async def recent_chats(dsn: str, tenant: uuid.UUID, user: str, now: datetime) -> None:
    """Store the design's recent chats with real answers from the workspace engine. The engine
    reads as ``app_rw`` with the tenant set, so row level security applies as in the API."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from account_intelligence.replica import answers

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE app_rw"))
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
            )
            ws = await answers.load_workspace(conn, user)
            for title, question, minutes in RECENT_CHATS:
                at = now - timedelta(minutes=minutes)
                answer = answers.compose_local(question, ws, now)
                thread = (
                    await conn.execute(
                        text(
                            "INSERT INTO chat_threads (owner_user_id, title, turn_count,"
                            " created_at, updated_at)"
                            " VALUES (:u, :t, 1, :at, :at) RETURNING id"
                        ),
                        {"u": user, "t": title, "at": at},
                    )
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO chat_turns (thread_id, turn_number, request_id, model_id,"
                        " user_content,"
                        " assistant_content, status, reserved_usd, created_at, completed_at,"
                        " answer_json)"
                        " VALUES (:th, 1, :r, 'workspace-local', :q, :a, 'completed', 0, :at, :at,"
                        " CAST(:j AS jsonb))"
                    ),
                    {
                        "th": thread,
                        "r": uuid.uuid4(),
                        "q": question,
                        "a": answers.plain_text(answer["paragraphs"]),
                        "at": at,
                        "j": json.dumps(answer),
                    },
                )
    finally:
        await engine.dispose()


async def password_hash(password: str, email: str) -> str:
    from identity import local_password

    return str(await local_password.hash_password(password, email=email))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--reset", action="store_true", help="remove the demo clients first")
    args = parser.parse_args()
    if os.environ.get("APP_ENV") not in ALLOWED_ENVS:
        print("Refusing: APP_ENV must be dev, local or test.", file=sys.stderr)
        return 2
    dsn = os.environ.get("MIGRATOR_DATABASE_URL")
    password = os.environ.get("DEMO_PASSWORD")
    if not dsn or not password:
        print("Set MIGRATOR_DATABASE_URL and DEMO_PASSWORD.", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    people = {
        "priya@meridian.example": ("priya-nair", "Priya Nair", 0),
        "dan@meridian.example": ("dan-meridian", "Dan Ruiz", 0),
        "l.m@northline.example": ("lm-northline", "L. Moreno", 1),
    }
    hashes = {e: asyncio.run(password_hash(password, e)) for e in people}
    with psycopg.connect(dsn, autocommit=False) as conn:
        exists = conn.execute("SELECT 1 FROM tenants WHERE name = %s", (MERIDIAN,)).fetchone()
        if exists and not args.reset:
            print("The demo workspace exists. Use --reset to seed it again.", file=sys.stderr)
            return 1
        if args.reset:
            reset(conn)
        seed = Seeder(conn, now)
        meridian = seed.tenant(MERIDIAN)
        seed.client_settings(meridian, pool=2000, cap=34)
        for email in ("priya@meridian.example", "dan@meridian.example"):
            subject, display, idle = people[email]
            seed.member(meridian, subject, email, display, hashes[email], idle)
        ids = {}
        for builder in (highmark, cambia, amerihealth, cigna, medica, availity):
            acct = builder()
            ids[acct.name] = str(seed.account(meridian, "priya-nair", acct))
        northline = seed.tenant(NORTHLINE)
        seed.client_settings(northline, pool=3000, cap=34)
        subject, display, idle = people["l.m@northline.example"]
        seed.member(
            northline,
            subject,
            "l.m@northline.example",
            display,
            hashes["l.m@northline.example"],
            idle,
        )
        other = cigna()
        other.narrative = (
            "Northline's own view of Cigna: a separate workspace with its own findings."
        )
        seed.account(northline, subject, other)
        seed.library(meridian)
        conn.commit()
    asyncio.run(recent_chats(dsn, meridian, "priya-nair", now))
    print(
        json.dumps(
            {"meridian": str(meridian), "northline": str(northline), "accounts": ids}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
