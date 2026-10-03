"""Synthetic in-memory people adapters and the corpus loader (spec 9). No real data.

``Corpus`` reads ``tests/fixtures/synthetic/people/corpus.yaml`` and builds scripted fakes. Every
paid port goes through ``FakeLedger``, which behaves like the connector ledger: a call with the
same arguments in the same run is replayed for free; ``crash_after_response`` simulates a kill
between the provider response and the ledger write (the call is paid, not recorded, and repeats
on resume: the documented one-call bound). ``paid`` counts provider calls. ``Kill`` points in the
repo simulate a crash at the named step.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from people.errors import NotConfigured
from people.models import (
    Mention,
    PersonPage,
    Post,
    PostsBatch,
    Profile,
    RawPerson,
    RawPost,
    RawProfile,
)
from people.ports import SourceUnavailable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/synthetic/people"
ACCOUNT = "synthetic-acme.example.com"


class Crash(RuntimeError):
    """A simulated process kill."""


@dataclass
class FakeLedger:
    """Per-run replay of identical calls, like the connector ledger."""

    entries: dict[str, Any] = field(default_factory=dict)
    paid: int = 0
    calls: list[str] = field(default_factory=list)
    crash_after_response: set[str] = field(default_factory=set)
    costs: dict[str, float | None] = field(default_factory=dict)

    def ref(self, op: str, args: Any) -> str:
        return hashlib.sha256(
            json.dumps([op, args], sort_keys=True, default=str).encode()
        ).hexdigest()

    def call(self, op: str, args: Any, produce: Any, cost: float | None = 0.02) -> tuple[str, Any]:
        key = self.ref(op, args)
        self.calls.append(op)
        if key in self.entries:
            return key, self.entries[key]
        self.paid += 1
        value = produce(key)
        if key in self.crash_after_response:
            self.crash_after_response.discard(key)
            raise Crash(f"killed after the provider answered {op}")
        self.entries[key] = value
        self.costs[key] = cost
        return key, value


def _raw(d: dict[str, Any], ref: str, origin: str = "title_search") -> RawPerson:
    return RawPerson(
        source_record_id=f"{d['id']}",
        origin=origin,  # type: ignore[arg-type]
        provider_code=d.get("provider", "p_people"),
        raw_ref=f"raw/{ref[:12]}",
        call_ref=ref,
        retrieved_at="2026-10-03T00:00:00+00:00",
        name=d.get("name"),
        title=d.get("title"),
        profile_url=d.get("url"),
        employer_domain=d.get("employer_domain"),
        is_current=d.get("is_current"),
        search_domain=ACCOUNT,
        fields_dropped=d.get("fields_dropped", 0),
    )


class FakeSearch:
    """Scripted title and association search with cursors."""

    def __init__(self, script: dict[str, Any], ledger: FakeLedger) -> None:
        self.script, self.ledger = script, ledger
        self.requests: list[tuple[str, str | None]] = []

    def _page(self, source_id: str, cursor: str | None, args: Any) -> PersonPage:
        entry = self.script.get(source_id)
        self.requests.append((source_id, cursor))
        if entry is None:
            raise SourceUnavailable("not_available_yet")
        if "unavailable" in entry:
            raise SourceUnavailable(entry["unavailable"])
        if entry.get("fail"):
            ref = self.ledger.ref("fail:" + source_id, [cursor])
            raise SourceUnavailable("source_unavailable", ref)
        index = 0 if cursor is None else entry.get("cursor_pages", {}).get(cursor, 0)
        page = entry["pages"][index]

        def produce(ref: str) -> PersonPage:
            return PersonPage(
                records=tuple(_raw(d, ref) for d in page["records"]),
                next_cursor=page["next_cursor"],
                call_ref=ref,
                reported_count=len(page["records"]),
            )

        _, value = self.ledger.call(source_id, [args, cursor], produce)
        return value  # type: ignore[no-any-return]

    async def search_titles(
        self, lane: str, pass_no: int, titles: tuple[str, ...], cursor: str | None
    ) -> PersonPage:
        return self._page(f"title:{lane}:{pass_no}", cursor, list(titles))

    async def search_association(
        self, lane: str, functions: tuple[str, ...], cursor: str | None
    ) -> PersonPage:
        return self._page(f"association:{lane}", cursor, list(functions))


class FakeMentions:
    def __init__(self, mentions: Sequence[Mention], configured: bool = True) -> None:
        self.items, self.configured = tuple(mentions), configured

    async def mentions(self) -> tuple[Mention, ...]:
        if not self.configured:
            raise NotConfigured("mentions")
        return self.items


class FakeResolver:
    def __init__(self, script: dict[str, Any], ledger: FakeLedger, fail: bool = False) -> None:
        self.script, self.ledger, self.fail = script, ledger, fail

    async def resolve(self, name: str, attempt: int) -> PersonPage:
        if self.fail:
            raise SourceUnavailable(
                "source_unavailable", self.ledger.ref("resolve-fail", [name, attempt])
            )
        answers = (self.script.get(name) or {}).get(attempt, [])

        def produce(ref: str) -> PersonPage:
            return PersonPage(records=tuple(_raw(d, ref, "mention") for d in answers), call_ref=ref)

        _, value = self.ledger.call("resolve", [name, attempt], produce)
        return value  # type: ignore[no-any-return]


class FakeProfiles:
    def __init__(
        self, script: dict[str, Any], ledger: FakeLedger, provider: str = "p_people"
    ) -> None:
        self.script, self.ledger, self.provider = script, ledger, provider
        self.pulled: list[str] = []

    async def pull(self, profile_url: str) -> RawProfile:
        entry = self.script.get(profile_url)
        if entry is None or entry.get("fail"):
            raise SourceUnavailable(
                "source_unavailable", self.ledger.ref("profile-fail", [profile_url])
            )
        if entry.get("budget"):
            raise SourceUnavailable("budget_cap")
        self.pulled.append(profile_url)

        def produce(ref: str) -> RawProfile:
            return RawProfile(
                profile_url=profile_url,
                provider_code=self.provider,
                raw_ref=f"raw/{ref[:12]}",
                call_ref=ref,
                fields={k: v for k, v in entry.items() if k not in ("fail", "budget")},
            )

        _, value = self.ledger.call("profile", [profile_url], produce)
        return value  # type: ignore[no-any-return]


class FakePosts:
    def __init__(
        self,
        script: dict[str, Any],
        ledger: FakeLedger,
        cost: float | None,
        activity_known: bool = True,
    ) -> None:
        self.script, self.ledger, self.cost, self.activity_known = (
            script,
            ledger,
            cost,
            activity_known,
        )
        self.pulled: list[str] = []

    async def pull(self, profile_url: str, since: date) -> PostsBatch:
        self.pulled.append(profile_url)
        items = self.script.get(profile_url, [])

        def produce(ref: str) -> PostsBatch:
            return PostsBatch(
                posts=tuple(
                    RawPost(
                        post_url=i["url"],
                        posted_at=i.get("posted_at"),
                        text=i.get("text", ""),
                        raw_ref=f"raw/{ref[:12]}",
                        call_ref=ref,
                    )
                    for i in items
                ),
                call_ref=ref,
                activity_known=self.activity_known,
            )

        _, value = self.ledger.call("posts", [profile_url, since.isoformat()], produce, self.cost)
        return value  # type: ignore[no-any-return]


class FakeCosts:
    def __init__(self, ledger: FakeLedger) -> None:
        self.ledger = ledger

    async def cost_usd(self, call_ref: str) -> float | None:
        return self.ledger.costs.get(call_ref)


class FakeLlm:
    """The scripted extractor: answers chosen by a phrase of the text; echoes the person key."""

    def __init__(self, script: dict[str, Any], raw: dict[str, Any] | None = None) -> None:
        self.script = script
        self.raw = raw or {}  # phrase -> exact raw answer (lying tests)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, person_key: str, text: str, canary: str) -> object:
        self.calls.append((person_key, text))
        for phrase, answer in self.raw.items():
            if phrase in text:
                return answer
        for phrase, answer in self.script.items():
            if phrase in text:
                return {"person_key": person_key, **answer}
        return {"person_key": person_key, "findings": []}


@dataclass
class StoredPageRow:
    source_id: str
    page_no: int
    cursor_in: str | None
    page: PersonPage


class FakeRepo:
    """In-memory ``PeopleRepo`` with kill points: set ``kill_at`` to a method name and the
    ``kill_after`` call count to crash there (the write is NOT applied)."""

    def __init__(self) -> None:
        self.record: Any = None
        self.pages: dict[tuple[str, int], StoredPageRow] = {}
        self.profiles: dict[str, Profile] = {}
        self.posts: dict[str, tuple[Post, ...]] = {}
        self.kill_at: str | None = None
        self.kill_after = 0
        self.writes: dict[str, int] = {}

    def _maybe_kill(self, name: str) -> None:
        self.writes[name] = self.writes.get(name, 0) + 1
        if self.kill_at == name and self.writes[name] > self.kill_after:
            self.kill_at = None
            raise Crash(f"killed in {name}")

    async def load_run(self, run_id: str) -> Any:
        return self.record

    async def load_pages(self, run_id: str) -> list[StoredPageRow]:
        return [self.pages[k] for k in sorted(self.pages)]

    async def save_page(
        self, run_id: str, source_id: str, page_no: int, cursor_in: str | None, page: PersonPage
    ) -> None:
        self._maybe_kill("save_page")
        self.pages.setdefault(
            (source_id, page_no), StoredPageRow(source_id, page_no, cursor_in, page)
        )

    async def stored_profiles(self, profile_urls: Sequence[str]) -> dict[str, Profile]:
        return {u: self.profiles[u] for u in profile_urls if u in self.profiles}

    async def save_profiles(self, run_id: str, profiles: Sequence[Profile]) -> None:
        self._maybe_kill("save_profiles")
        for p in profiles:
            self.profiles.setdefault(p.profile_url, p)

    async def stored_posts(self, run_id: str) -> dict[str, tuple[Post, ...]]:
        return dict(self.posts)

    async def save_posts(self, run_id: str, profile_url: str, posts: Sequence[Post]) -> None:
        self._maybe_kill("save_posts")
        self.posts.setdefault(profile_url, tuple(posts))

    async def save_run(self, run_id: str, record: Any) -> None:
        self._maybe_kill("save_run")
        self.record = record


class Recorder:
    """Records hand-offs to the tracker, grading and the source registry."""

    def __init__(self) -> None:
        self.evidence: list[dict[str, Any]] = []
        self.claims: list[Any] = []
        self.codes: dict[str, str] = {}

    async def record(
        self,
        row_key: str,
        call_refs: Sequence[str],
        *,
        expected_calls: int,
        cap_hit: bool,
        not_attempted_reason: str | None,
    ) -> None:
        self.evidence.append(
            {
                "row": row_key,
                "refs": list(call_refs),
                "expected": expected_calls,
                "cap_hit": cap_hit,
                "reason": not_attempted_reason,
            }
        )

    async def submit_claims(self, claims: Sequence[Any]) -> None:
        self.claims.extend(claims)

    async def code_for(self, url: str) -> str:
        return self.codes.setdefault(url, f"S-{len(self.codes) + 1:04d}")


class FakeRunInput:
    def __init__(
        self, corpus: Corpus, *, regions: tuple[str, ...] = (), refs: Any = "corpus"
    ) -> None:
        self.corpus, self._regions = corpus, regions
        self._refs = corpus.reference_employers if refs == "corpus" else refs

    async def lanes_off(self) -> frozenset[str]:
        return self.corpus.lanes_off

    async def as_of(self) -> date:
        return self.corpus.as_of

    async def regions(self) -> tuple[str, ...]:
        return self._regions

    async def reference_employers(self) -> tuple[str, ...] | None:
        return self._refs

    async def people_budget_usd(self) -> float | None:
        return self.corpus.budget_usd


class Corpus:
    """The synthetic corpus and its scripted fakes."""

    def __init__(self, path: Path | None = None) -> None:
        data = yaml.safe_load((path or FIXTURES / "corpus.yaml").read_text())
        assert data["label"] == "synthetic"
        self.data = data
        self.account = data["account_domain"]
        self.as_of = date.fromisoformat(data["as_of"])
        self.lanes_off = frozenset(data["lanes_off"])
        self.reference_employers = tuple(data["reference_employers"])
        self.budget_usd = float(data["budget_usd"])
        self.ledger = FakeLedger()

    def mentions(self) -> list[Mention]:
        return [
            Mention(name=m["name"], mention_url=m["url"], sentence=m["sentence"])
            for m in self.data["mentions"]
        ]


def oracle() -> dict[str, Any]:
    data = yaml.safe_load((FIXTURES / "oracle.yaml").read_text())
    assert data["label"] == "synthetic"
    return data  # type: ignore[no-any-return]
