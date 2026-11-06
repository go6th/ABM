"""One event is one event (DS1, DS2): recording a graded candidate as a signal event.

* The fingerprint (``signals.fingerprint``) is unique per account: the same event from a second
  outlet, a re-fetch, a replayed webhook or a later probe ATTACHES its source as evidence to the
  existing event and creates nothing new.
* A near-duplicate with a different fingerprint is related to the account's recent events of the
  same type by ``signals.cluster``: ``same`` attaches as evidence, ``ambiguous`` creates its own
  event LINKED to the other (one alert lists both, D-73), ``distinct`` creates a new event.
* The score, its components and the config version are stored with the event.

The one writer of ``signal_events``, ``signal_event_evidence`` and ``signal_event_links``.
``import_events`` records the signals an uploaded Account Summary states (workspace import,
2026-11-03): one event per signal fact, keyed by what it says, scored 0 (the platform did not
observe it) and typed from its words, so the Signals screen shows every signal of the file.

GUARANTEE(test_one_event_is_one_event_and_evidence_attaches)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text

from signals import cluster
from signals.score import Score

Via = Literal["poll", "webhook", "refresh"]


@dataclass(frozen=True)
class EventInput:
    """A graded candidate (the fact exists: the grading engine recorded it)."""

    account_id: UUID
    fingerprint: str
    signal_type: str
    event_type: str
    entity: str
    title: str
    bucket: int
    grade: str
    lead: bool
    sensitive: bool
    fact_id: UUID
    delta_item_id: UUID | None
    score: Score
    source_url: str
    source_code: str | None
    source_tier: str
    via: Via
    detected_at: datetime


@dataclass(frozen=True)
class ImportedSignal:
    """One signal stated by an uploaded file: the fact the import recorded and its words."""

    fact_id: UUID
    attribute_key: str
    title: str
    grade: str
    bucket: int
    detected_at: datetime
    source_url: str | None = None
    source_code: str | None = None


_TYPE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "leadership_change",
        (
            "cio",
            "cto",
            "ceo",
            "cfo",
            "chief",
            "appoint",
            "named",
            "joins",
            "hired as",
            "head of",
            "vp ",
            "vice president",
            "director",
        ),  # fmt: skip
    ),
    (
        "build_role_posting",
        (
            "hiring",
            "posting",
            "postings",
            "recruit",
            "vacanc",
            "opening",
            "role",
            "engineer",
            "administrator",
            "developer",
        ),  # fmt: skip
    ),
    ("funding_ma", ("acqui", "merger", "funding", "raised", "investment", "ipo", "divest")),
    ("facility_expansion", ("plant", "facility", "factory", "office", "expan", "opens", "site")),
    ("exec_speaking", ("keynote", "speaks", "speaking", "panel", "conference", "webinar")),
    ("vendor_customer_story", ("customer story", "case study", "partner", "reseller", "go-live")),
    (
        "tech_detection",
        (
            "salesforce",
            "sap",
            "servicenow",
            "veeva",
            "tableau",
            "crm",
            "erp",
            "platform",
            "migrat",
            "implement",
            "upgrade",
            "cloud",
        ),  # fmt: skip
    ),
)


def signal_type_of(text_value: str) -> str:
    """The scoring vocabulary's type a signal's words point at (``other`` when none do)."""
    lowered = f" {text_value.casefold()} "
    for kind, words in _TYPE_WORDS:
        if any(w in lowered for w in words):
            return kind
    return "news_event" if "news" in lowered or "announce" in lowered else "other"


async def import_events(s: Any, account_id: UUID, items: list[ImportedSignal]) -> int:
    """Record the signals of an uploaded file as events (see the module docstring); an event the
    account already holds (same words) is left as is. Returns the events created."""
    created = 0
    for item in items:
        title = " ".join(item.title.split())[:500]
        if not title:
            continue
        grade = (
            item.grade
            if item.grade in ("confirmed", "probable", "hypothesis", "lead")
            else ("hypothesis")
        )
        fingerprint = hashlib.sha256(
            f"{account_id}:{item.attribute_key}:{title.casefold()}".encode()
        ).hexdigest()
        kind = signal_type_of(f"{item.attribute_key} {title}")
        event_id = await _insert(
            s,
            EventInput(
                account_id=account_id,
                fingerprint=fingerprint,
                signal_type=kind,
                event_type=item.attribute_key.rsplit(".", 1)[-1][:80] or kind,
                entity=title[:300],
                title=title,
                bucket=max(1, min(int(item.bucket), 3)),
                grade=grade,
                lead=grade == "lead",
                sensitive=False,
                fact_id=item.fact_id,
                delta_item_id=None,
                score=Score(total=0, components=(("imported", 0),), config_version="imported"),
                source_url=item.source_url or "",
                source_code=item.source_code,
                source_tier="t3",
                via="refresh",
                detected_at=item.detected_at,
            ),
        )
        if event_id is None:
            continue
        created += 1
        if item.source_url:
            await _evidence(
                s,
                event_id,
                EventInput(
                    account_id=account_id,
                    fingerprint=fingerprint,
                    signal_type=kind,
                    event_type=kind,
                    entity=title[:300],
                    title=title,
                    bucket=1,
                    grade=grade,
                    lead=grade == "lead",
                    sensitive=False,
                    fact_id=item.fact_id,
                    delta_item_id=None,
                    score=Score(total=0, components=(), config_version="imported"),
                    source_url=item.source_url,
                    source_code=item.source_code,
                    source_tier="t3",
                    via="refresh",
                    detected_at=item.detected_at,
                ),
            )
    return created


@dataclass(frozen=True)
class Recorded:
    """What recording did."""

    event_id: UUID
    outcome: Literal["created", "attached", "linked"]


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


async def _evidence(s: Any, event_id: UUID, x: EventInput) -> None:
    await s.execute(
        text(
            "INSERT INTO signal_event_evidence (event_id, source_code, source_tier, url_sha256,"
            " via,"
            " at) VALUES (:e, :c, :t, :u, :v, :at) ON CONFLICT (event_id, url_sha256) DO NOTHING"
        ),
        {
            "e": event_id,
            "c": x.source_code,
            "t": x.source_tier,
            "u": _url_hash(x.source_url),
            "v": x.via,
            "at": x.detected_at,
        },
    )


async def _insert(s: Any, x: EventInput) -> UUID | None:
    row = (
        await s.execute(
            text(
                "INSERT INTO signal_events (account_id, fingerprint, signal_type, event_type,"
                " entity,"
                " title, bucket, grade, lead, sensitive, fact_id, delta_item_id, score, components,"
                " config_version, detected_at) VALUES (:a, :fp, :st, :et, :en, :ti, :b, :g, :l,"
                " :sen, :f,"
                " :d, :sc, CAST(:comp AS jsonb), :cv, :at)"
                " ON CONFLICT (tenant_id, account_id, fingerprint) DO NOTHING RETURNING id"
            ),
            {
                "a": x.account_id,
                "fp": x.fingerprint,
                "st": x.signal_type,
                "et": x.event_type,
                "en": x.entity,
                "ti": x.title,
                "b": x.bucket,
                "g": x.grade,
                "l": x.lead,
                "sen": x.sensitive,
                "f": x.fact_id,
                "d": x.delta_item_id,
                "sc": x.score.total,
                "comp": json.dumps([list(c) for c in x.score.components]),
                "cv": x.score.config_version,
                "at": x.detected_at,
            },
        )
    ).first()
    return None if row is None else UUID(str(row.id))


async def record(s: Any, x: EventInput, *, same: float, ambiguous: float) -> Recorded:
    """Record a candidate (see the module docstring)."""
    existing = (
        await s.execute(
            text("SELECT id FROM signal_events WHERE account_id = :a AND fingerprint = :fp"),
            {"a": x.account_id, "fp": x.fingerprint},
        )
    ).first()
    if existing is not None:
        await _evidence(s, existing.id, x)
        return Recorded(existing.id, "attached")
    near = (
        await s.execute(
            text(
                "SELECT id, event_type, entity, title, bucket FROM signal_events WHERE account_id"
                " = :a"
                " AND signal_type = :st AND bucket BETWEEN :b - 1 AND :b + 1 ORDER BY"
                " detected_at, id"
                " LIMIT 50"
            ),
            {"a": x.account_id, "st": x.signal_type, "b": x.bucket},
        )
    ).all()
    me = cluster.Candidate(x.event_type, x.entity, x.title, x.bucket)
    ambiguous_with: list[UUID] = []
    for r in near:  # LOOP(cluster scan): cap=50 recent events; exit=first same, or end
        rel = cluster.relate(
            me,
            cluster.Candidate(r.event_type, r.entity, r.title, r.bucket),
            same=same,
            ambiguous=ambiguous,
        )
        if rel == "same":
            await _evidence(s, r.id, x)
            return Recorded(r.id, "attached")
        if rel == "ambiguous":
            ambiguous_with.append(r.id)
    new_id = await _insert(s, x)
    if new_id is None:  # a concurrent writer recorded the same fingerprint first
        row = (
            await s.execute(
                text("SELECT id FROM signal_events WHERE account_id = :a AND fingerprint = :fp"),
                {"a": x.account_id, "fp": x.fingerprint},
            )
        ).one()
        await _evidence(s, row.id, x)
        return Recorded(row.id, "attached")
    await _evidence(s, new_id, x)
    for other in ambiguous_with:
        a, b = sorted((new_id, other), key=str)
        await s.execute(
            text(
                "INSERT INTO signal_event_links (event_a, event_b, relation, at)"
                " VALUES (:a, :b, 'ambiguous', :at) ON CONFLICT DO NOTHING"
            ),
            {"a": a, "b": b, "at": x.detected_at},
        )
    return Recorded(new_id, "linked" if ambiguous_with else "created")


async def linked(s: Any, event_id: UUID) -> list[UUID]:
    """Events linked to this one as ambiguous."""
    rows = (
        await s.execute(
            text(
                "SELECT event_b AS o FROM signal_event_links WHERE event_a = :e UNION"
                " SELECT event_a FROM signal_event_links WHERE event_b = :e"
            ),
            {"e": event_id},
        )
    ).all()
    return sorted((UUID(str(r.o)) for r in rows), key=str)
