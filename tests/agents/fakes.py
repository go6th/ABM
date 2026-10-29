"""Scripted fakes for the ABM agent layer (synthetic data only; no provider, model or cloud calls).

* ``StubLedgerProvider`` behaves like the connector ledger: pull once per ``(run, need, query)``
  (a replay is free), the real ``spend.gate.decide`` before every paid call, crash injection.
* ``FakeGrading`` stands in for the grading engine: grades only from what the agent layer passes
  (read this run -> confirmed; unread match -> lead; otherwise probable). Fact ids are content
  hashes, so the same world gives the same facts in any run (G17 comparisons).
* ``FakeLlm`` answers by task kind with honest scripts; tests override a task kind with a lying
  script. Every answer is validated against the requested schema, as the router does.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from agents.config import SkillConfig
from agents.entity import EntityLock, build_lock
from agents.manifest import PullManifest
from agents.ports import (
    CapPause,
    ConsentRecord,
    GradeBatch,
    GradingCandidate,
    InternalHistorySettings,
    PipelineOutcome,
    ProviderFailed,
    ProviderResult,
    RowOutcome,
    RunContext,
    RunRecord,
    SenseCheckRecord,
    SourceItem,
    TenantAgentSettings,
)
from agents.schemas import EvidenceRef, FactCandidate, PayloadView, PersonCandidate, StoredFact
from spend.gate import RunCapState, decide

SYNTHETIC = "synthetic"
FREE_NEEDS = frozenset({"web_search", "page_read"})
PRICES = {
    "web_search": 0,
    "page_read": 0,
    "scrape": 10_000,
    "structured_signals": 50_000,
    "people_by_title": 20_000,
    "people_by_technology": 20_000,
    "profile_enrichment": 20_000,
}


def h(text: str, n: int = 12) -> str:
    """Short sha256 hex."""
    return hashlib.sha256(text.encode()).hexdigest()[:n]


class FakeClock:
    """A virtual clock."""

    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime(2026, 10, 8, 9, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


@dataclass(frozen=True)
class Page:
    """One synthetic page a provider returns for a row."""

    url: str
    body: str
    count: int = 1
    match_kind: str | None = None


@dataclass
class World:
    """A synthetic account: pages per coverage row, failing rows, sense-check proposals."""

    name: str
    domain: str
    pages: dict[str, list[Page]] = field(default_factory=dict)
    failing_rows: set[str] = field(default_factory=set)
    proposals: list[dict[str, str]] = field(default_factory=list)
    label: str = SYNTHETIC


class FakeCredit:
    """Caps per run (approved budgets), spend, prices and approval requests."""

    def __init__(self) -> None:
        self.budgets: dict[UUID, int] = {}
        self.spent: dict[UUID, int] = defaultdict(int)
        self.requests: list[tuple[UUID, int, int]] = []

    def approve(self, run_id: UUID, budget_micros: int) -> None:
        self.budgets[run_id] = budget_micros

    async def cap_state(self, ctx: RunContext) -> RunCapState:
        budget = self.budgets.get(ctx.run_id)
        return RunCapState(
            cap_required=True,
            cap_version=1 if budget is not None else None,
            budget_micros=budget,
            spent_micros=self.spent[ctx.run_id],
            overshoot_micros=0,
            sample_calls_left={},
        )

    async def price(self, need: str) -> tuple[int, bool]:
        return PRICES[need], True

    async def propose_cap(self, ctx: RunContext) -> bool:
        self.proposals = getattr(self, "proposals", 0) + 1
        return ctx.run_id in self.budgets

    async def accept_tokens(self, ctx: RunContext) -> None:
        self.tokens_accepted = getattr(self, "tokens_accepted", 0) + 1

    async def request_approval(
        self, ctx: RunContext, proposals: Sequence[Mapping[str, Any]], total_micros: int
    ) -> str:
        self.requests.append((ctx.run_id, len(proposals), total_micros))
        return f"appr-{len(self.requests)}"


class Killed(RuntimeError):
    """Injected crash (the process dies mid-pull)."""


class StubLedgerProvider:
    """See the module docstring."""

    def __init__(self, world: World, cfg: SkillConfig, credit: FakeCredit) -> None:
        self.world, self.cfg, self.credit = world, cfg, credit
        self.calls: dict[tuple[UUID, str, str], ProviderResult] = {}
        self.paid_calls = 0
        self.crash_after: int | None = None
        self.new_calls = 0
        # the entity lock the stub returns: the world's home page names the account unless a
        # test sets ``lookalike`` (the home page belongs to another company)
        self.lookalike = False
        self.lock_calls = 0

    async def entity_lock(self, ctx: RunContext) -> EntityLock:
        from agents.config import skill_config

        self.lock_calls += 1
        stop = skill_config().scope.name_stopwords
        if self.lookalike:
            lock = build_lock(
                legal_name=ctx.account_name,
                primary_domain=ctx.domain,
                stopwords=stop,
                home_url=f"https://{ctx.domain}/",
                final_url=None,
                home_title="Another Company, 3D studio",
                home_text="We build renders for architects.",
            )
            # the world's domain carries the brand, so force the look-alike state the real lock
            # reaches for a near-name domain (vizent.co for Vizient)
            return replace(lock, confirmed=False, mismatch=True)
        return build_lock(
            legal_name=ctx.account_name,
            primary_domain=ctx.domain,
            stopwords=stop,
            home_url=f"https://{ctx.domain}/",
            final_url=f"https://www.{ctx.domain}/",
            home_title=ctx.account_name,
            home_text=f"{ctx.account_name} home page",
            search_hits=[(f"https://{ctx.domain}/", ctx.account_name)],
        )

    async def call(self, ctx: RunContext, need: str, query: str, row_key: str) -> ProviderResult:
        key = (ctx.run_id, need, query)
        if key in self.calls:
            return self.calls[key]
        if self.crash_after is not None and self.new_calls >= self.crash_after:
            self.crash_after = None
            raise Killed("process killed")
        price = PRICES[need]
        decision = decide(await self.credit.cap_state(ctx), need, price, True, FREE_NEEDS)
        if not decision.allowed:
            raise CapPause(decision.reason)
        if row_key in self.world.failing_rows:
            raise ProviderFailed("provider_down")
        pages = self.world.pages.get(row_key, [])
        index = sum(1 for k in self.calls if k[0] == ctx.run_id and k[1] == need)
        page = pages[index % len(pages)] if pages else None
        self.new_calls += 1
        if price:
            self.paid_calls += 1
            self.credit.spent[ctx.run_id] += price
        items = (SourceItem(page.url, page.body, page.match_kind),) if page and page.body else ()
        result = ProviderResult(
            items=items,
            result_count=page.count if page else 0,
            cost_micros=price,
            tool_category=need,
            call_refs=(f"call-{h(f'{ctx.run_id}{need}{query}')}",),
        )
        self.calls[key] = result
        return result


class FakeInternal:
    """D-98 internal history connector stand-in (private content)."""

    def __init__(self, body: str = "") -> None:
        self.body = body
        self.searches = 0

    async def search(self, ctx: RunContext, row_key: str, query: str) -> ProviderResult:
        self.searches += 1
        return ProviderResult(
            items=(SourceItem(f"https://internal.invalid/{row_key}", self.body),),
            result_count=1,
            cost_micros=0,
            tool_category="internal_history",
            call_refs=(f"int-{h(row_key + query)}",),
        )


class FakePayloads:
    """Neutral-key payload store."""

    def __init__(self) -> None:
        self.items: dict[str, tuple[UUID, PayloadView]] = {}

    async def put(self, ctx: RunContext, row_key: str, item: SourceItem, visibility: str) -> str:
        ref = f"pl/{h(f'{ctx.run_id}{row_key}{item.url}{item.text}{visibility}', 16)}"
        view = PayloadView.model_validate(
            {
                "payload_ref": ref,
                "row_key": row_key,
                "url": item.url,
                "text": item.text,
                "visibility": "private" if visibility == "private" else "public",
                "match_kind": item.match_kind,
            }
        )
        self.items[ref] = (ctx.tenant_id, view)
        return ref

    async def view(self, tenant_id: UUID, ref: str) -> PayloadView:
        owner, view = self.items[ref]
        if owner != tenant_id:
            raise KeyError(ref)
        return view


class FakeManifests:
    """Insert-only manifests."""

    def __init__(self) -> None:
        self.by_run: dict[UUID, PullManifest] = {}
        self.saves = 0

    async def save(self, manifest: PullManifest) -> None:
        self.saves += 1
        self.by_run.setdefault(manifest.run_id, manifest)

    async def for_run(self, tenant_id: UUID, run_id: UUID) -> PullManifest | None:
        m = self.by_run.get(run_id)
        return m if m is not None and m.tenant_id == tenant_id else None

    async def latest(self, tenant_id: UUID, account_key: str) -> PullManifest | None:
        mine = [
            m
            for m in self.by_run.values()
            if m.tenant_id == tenant_id and m.account_key == account_key
        ]
        return max(mine, key=lambda m: m.created_at) if mine else None


class FakeConsent:
    """Consent records."""

    def __init__(self, record: ConsentRecord | None = None) -> None:
        self.record = record

    async def current(self, tenant_id: UUID, scope: str) -> ConsentRecord | None:
        r = self.record
        return r if r is not None and r.tenant_id == tenant_id else None


class FakeSettings:
    """Tenant settings."""

    def __init__(self, settings: TenantAgentSettings | None = None) -> None:
        self.settings = settings or TenantAgentSettings(
            pursuing_client="Synthetic Partner Co", lanes=("salesforce", "data_ai")
        )

    async def get(self, tenant_id: UUID) -> TenantAgentSettings:
        return self.settings


class FakeGrading:
    """See the module docstring."""

    def __init__(self, *, promote_matches: bool = False) -> None:
        self.promote_matches = promote_matches
        self.facts_by_run: dict[UUID, dict[str, StoredFact]] = defaultdict(dict)
        self.superseded: dict[UUID, set[str]] = defaultdict(set)
        self.people: dict[UUID, list[PersonCandidate]] = defaultdict(list)
        self.graded_items = 0

    async def grade(self, ctx: RunContext, items: Sequence[GradingCandidate]) -> GradeBatch:
        facts, index, drops = [], [], Counter[str]()
        for i, item in enumerate(items):
            c = item.candidate
            if not item.urls:
                drops["no_link"] += 1
                continue
            if item.read_this_run:
                grade = "confirmed"
            elif c.origin in ("keyword_match", "date_range"):
                grade = "confirmed" if self.promote_matches else "lead"
            else:
                grade = "probable"
            fid = f"f-{h(f'{c.row_key}|{c.attribute}|{c.value}|{item.visibility}')}"
            fact = StoredFact(
                fact_id=fid,
                row_key=c.row_key,
                attribute=c.attribute,
                value=c.value,
                grade=grade,
                visibility="private" if item.visibility == "private" else "public",
            )
            if fid in self.facts_by_run[ctx.run_id] or fid in self.superseded[ctx.run_id]:
                drops["duplicate"] += 1
                continue
            self.facts_by_run[ctx.run_id][fid] = fact
            self.graded_items += 1
            facts.append(fact)
            index.append(i)
        return GradeBatch(tuple(facts), tuple(index), dict(drops))

    async def mark_conflicting(
        self, ctx: RunContext, fact_ids: Sequence[str], reason: str
    ) -> tuple[str, ...]:
        out = []
        current = self.facts_by_run[ctx.run_id]
        for fid in fact_ids:
            old = current.pop(fid, None)
            if old is None:
                continue
            self.superseded[ctx.run_id].add(fid)
            new = old.model_copy(update={"fact_id": f"{fid}-c", "grade": "conflicting"})
            current[new.fact_id] = new
            out.append(new.fact_id)
        return tuple(out)

    async def facts(self, ctx: RunContext) -> tuple[StoredFact, ...]:
        return tuple(sorted(self.facts_by_run[ctx.run_id].values(), key=lambda f: f.fact_id))

    async def record_people(
        self, ctx: RunContext, people: Sequence[PersonCandidate]
    ) -> tuple[str, ...]:
        self.people[ctx.run_id].extend(people)
        return tuple(f"p-{h(p.name)}" for p in people)


class FakePipelines:
    """The code pipelines after the reading step, scripted from the world: each ``attr: text``
    line of a job row's pages becomes one read, classified posting (a full-page read: the fact is
    graded confirmed) and each line of the detections row one dated detection (a date range:
    lead). Nothing here reads a model."""

    def __init__(
        self, world: World, cfg: SkillConfig, grading: FakeGrading, tracker: FakeTracker
    ) -> None:
        self.world, self.cfg, self.grading, self.tracker = world, cfg, grading, tracker
        self.jobs_runs = 0
        self.tech_runs = 0
        self.stack_runs = 0
        self.careers: list[str | None] = []

    def _lines(self, pipeline: str) -> list[tuple[str, str, str, str]]:
        out = []
        for key, row in self.cfg.plan.items():
            if row.pipeline != pipeline:
                continue
            for page in self.world.pages.get(key, []):
                for line in page.body.splitlines():
                    m = _LINE.match(line.strip())
                    if m and len(m.group(2)) >= 8:
                        out.append((key, m.group(1), m.group(2), page.url))
        return out

    async def _facts(
        self, ctx: RunContext, pipeline: str, origin: str, read: bool
    ) -> dict[str, int]:
        items = [
            GradingCandidate(
                candidate=FactCandidate(
                    row_key=row,
                    attribute=attr,
                    value=value[:300],
                    origin=origin,  # type: ignore[arg-type]
                    evidence=(EvidenceRef(payload_ref=f"pipe/{h(url)}", quote=value),),
                ),
                urls=(url,),
                visibility="public",
                read_this_run=read,
            )
            for row, attr, value, url in self._lines(pipeline)
        ]
        batch = await self.grading.grade(ctx, items)
        return {"graded": len(batch.facts)}

    async def run_jobs(self, ctx: RunContext, careers_url: str | None) -> PipelineOutcome:
        self.jobs_runs += 1
        self.careers.append(careers_url)
        # the pipeline reports its own rows (board, feed, read) with its calls as proof
        for key, row in self.cfg.plan.items():
            if row.pipeline == "jobs":
                await self.tracker.report(ctx, key, [f"pipe-{h(key)}"], None)
        graded = await self._facts(ctx, "jobs", "read", True)
        return PipelineOutcome({"jobs_claims": graded["graded"]}, {"jobs_gap": "converged"})

    async def write_detections(self, ctx: RunContext) -> PipelineOutcome:
        self.tech_runs += 1
        graded = await self._facts(ctx, "tech", "date_range", False)
        return PipelineOutcome({"tech_rows": graded["graded"]}, {"tech": "written"})

    async def write_stack(self, ctx: RunContext) -> PipelineOutcome:
        """The organisation tech stack step: counted, nothing stored in the fake."""
        self.stack_runs += 1
        return PipelineOutcome({"stack_rows": 0}, {"stack": "written"})

    async def run_people(self, ctx: RunContext) -> PipelineOutcome:
        """The people pipeline stand-in: every ``person: Name, Title at Account`` line of a
        people row's pages becomes a recorded person; the three rows are reported with proof."""
        self.people_runs = getattr(self, "people_runs", 0) + 1
        people: list[PersonCandidate] = []
        for key, row in self.cfg.plan.items():
            if row.pipeline != "people":
                continue
            await self.tracker.report(ctx, key, [f"pipe-{h(key)}"], None)
            for page in self.world.pages.get(key, []):
                for line in page.body.splitlines():
                    m = _LINE.match(line.strip())
                    if m and m.group(1) == "person":
                        name, _, rest = m.group(2).partition(",")
                        people.append(
                            PersonCandidate(
                                name=name.strip(),
                                title=rest.split(" at ")[0].strip(),
                                priority="A",
                                evidence=(
                                    EvidenceRef(
                                        payload_ref=f"pipe/{h(page.url)}", quote=m.group(2)
                                    ),
                                ),
                            )
                        )
        if people:
            await self.grading.record_people(ctx, people)
        return PipelineOutcome({"people_found": len(people)}, {"people_gap_loop": "converged"})


class FakeTracker:
    """Rows derived from evidence: a reason -> blocked, call refs -> done; downgrades to partial."""

    def __init__(self) -> None:
        self.rows_by_run: dict[UUID, dict[str, RowOutcome]] = defaultdict(dict)
        self.reports = 0

    async def report(
        self, ctx: RunContext, row_key: str, call_refs: Sequence[str], reason: str | None
    ) -> RowOutcome:
        self.reports += 1
        rows = self.rows_by_run[ctx.run_id]
        if reason:
            out = RowOutcome("blocked", reason, 0)
        else:
            prior = rows.get(row_key)
            queries = len(call_refs) + (prior.queries if prior else 0)
            out = RowOutcome(
                "done" if call_refs else "not_done", None if call_refs else "no_calls", queries
            )
        rows[row_key] = out
        return out

    async def rows(self, ctx: RunContext) -> Mapping[str, RowOutcome]:
        return dict(self.rows_by_run[ctx.run_id])

    async def mark_partial(self, ctx: RunContext, row_key: str, reason: str) -> RowOutcome:
        prior = self.rows_by_run[ctx.run_id].get(row_key)
        if prior is not None and prior.status == "blocked":
            return prior
        out = RowOutcome("partial", reason, prior.queries if prior else 0)
        self.rows_by_run[ctx.run_id][row_key] = out
        return out


class FakeRecords:
    """Sense-check and distill records."""

    def __init__(self) -> None:
        self.sense: dict[UUID, SenseCheckRecord] = {}
        self.distilled: dict[tuple[UUID, str], BaseModel] = {}
        self.sense_saves = 0
        self.locks: dict[str, dict[str, Any]] = {}  # by account key (the account's lock)

    async def save_entity_lock(self, ctx: RunContext, lock: Mapping[str, Any]) -> None:
        self.locks[ctx.account_key] = dict(lock)

    async def entity_lock(self, ctx: RunContext) -> Mapping[str, Any] | None:
        return self.locks.get(ctx.account_key)

    async def save_sense_check(self, ctx: RunContext, record: SenseCheckRecord) -> None:
        self.sense_saves += 1
        self.sense[ctx.run_id] = record

    async def sense_check(self, ctx: RunContext) -> SenseCheckRecord | None:
        return self.sense.get(ctx.run_id)

    async def save_distill(self, ctx: RunContext, audience: str, output: BaseModel) -> None:
        self.distilled[(ctx.run_id, audience)] = output


class FakeBuilder:
    """Builds deterministic text files from facts and distill output; ``plant`` adds a line."""

    def __init__(self, grading: FakeGrading, records: FakeRecords) -> None:
        self.grading, self.records = grading, records
        self.files: dict[str, tuple[str, ...]] = {}
        self.plant: str | None = None
        self.builds = 0

    async def build(self, ctx: RunContext, audience: str) -> tuple[str, ...]:
        self.builds += 1
        facts = await self.grading.facts(ctx)
        if audience == "client":
            facts = tuple(f for f in facts if f.visibility == "public")
        lines = [f"{f.attribute}: {f.value} [{f.grade}]" for f in facts]
        out = self.records.distilled.get((ctx.run_id, audience))
        if out is not None:
            for section in ("hypotheses", "questions", "narrative"):
                lines += [c.text for c in getattr(out, section)]
        if audience == "client" and self.plant:
            lines.append(self.plant)
        file_id = f"{audience}-{h(chr(10).join(lines))}"
        self.files[file_id] = tuple(lines)
        return (file_id,)

    async def texts(self, ctx: RunContext, file_id: str) -> tuple[str, ...]:
        return self.files[file_id]


class FakeGate:
    """The quality gate stand-in."""

    def __init__(self) -> None:
        self.failing: dict[str, list[str]] = {}

    async def check(self, ctx: RunContext) -> Mapping[str, list[str]]:
        return self.failing


class FakeDelivery:
    """Records releases; there is no outreach method at all."""

    def __init__(self) -> None:
        self.released: list[tuple[UUID, tuple[str, ...]]] = []
        self.withheld: list[tuple[UUID, tuple[str, ...]]] = []

    async def release(
        self, ctx: RunContext, file_ids: Sequence[str], withheld: Sequence[str] = ()
    ) -> None:
        self.released.append((ctx.run_id, tuple(file_ids)))
        if withheld:
            self.withheld.append((ctx.run_id, tuple(withheld)))


class FakeWallet:
    """Counts settlements per run."""

    def __init__(self) -> None:
        self.settlements: Counter[UUID] = Counter()

    async def settle_once(self, ctx: RunContext, platform_fault: bool) -> bool:
        self.settlements[ctx.run_id] += 1
        return self.settlements[ctx.run_id] == 1


class FakeRuns:
    """Run rows, idempotent per (tenant, key); tenant-scoped reads."""

    def __init__(self) -> None:
        self.by_key: dict[tuple[UUID, str], RunRecord] = {}
        self.by_id: dict[UUID, RunRecord] = {}

    async def create(self, record: RunRecord, idempotency_key: str) -> tuple[RunRecord, bool]:
        key = (record.tenant_id, idempotency_key)
        if key in self.by_key:
            return self.by_key[key], False
        self.by_key[key] = record
        self.by_id[record.run_id] = record
        return record, True

    async def get(self, tenant_id: UUID, run_id: UUID) -> RunRecord | None:
        r = self.by_id.get(run_id)
        return r if r is not None and r.tenant_id == tenant_id else None


# Model ---------------------------------------------------------------------------------------

Script = Callable[[Mapping[str, Any]], Mapping[str, Any]]
_LINE = re.compile(r"^([a-z_]+):\s*(.+)$")


def honest_extract(cfg: SkillConfig) -> Script:
    """One candidate per ``attr: sentence`` line of each payload (quoted verbatim)."""

    def run(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        out = []
        for p in payload["payloads"]:
            full = cfg.plan[p["row_key"]].need in ("scrape", "page_read")
            for line in p["text"].splitlines():
                m = _LINE.match(line.strip())
                if not m or m.group(1) == "person" or len(m.group(2)) < 8:
                    continue
                out.append(
                    {
                        "row_key": p["row_key"],
                        "attribute": m.group(1),
                        "value": m.group(2)[:300],
                        "lane": None,
                        "origin": "read" if full else "keyword_match",
                        "evidence": [{"payload_ref": p["payload_ref"], "quote": m.group(2)}],
                    }
                )
        return {"candidates": out}

    return run


def honest_people(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """``person: Name, Title at Account.`` lines become people."""
    people = []
    for p in payload["payloads"]:
        for line in p["text"].splitlines():
            m = _LINE.match(line.strip())
            if m and m.group(1) == "person":
                name, _, rest = m.group(2).partition(",")
                title = rest.split(" at ")[0].strip()
                people.append(
                    {
                        "name": name.strip(),
                        "title": title,
                        "priority": "A",
                        "evidence": [{"payload_ref": p["payload_ref"], "quote": m.group(2)}],
                    }
                )
    return {"people": people}


def honest_sense(world: World) -> Script:
    """Contradictions (same attribute, different values) and scripted proposals."""

    def run(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        by_attr: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        findings: list[dict[str, Any]] = []
        for f in payload["facts"]:
            if f["grade"] != "conflicting":
                by_attr[f["attribute"]].append(f)
            if "subsidiary" in f["value"].lower():
                findings.append(
                    {
                        "kind": "entity_boundary",
                        "fact_ids": [f["fact_id"]],
                        "note": "fact concerns a subsidiary",
                    }
                )
            if "not to be confused" in f["value"].lower():
                findings.append(
                    {
                        "kind": "adjacent_organisation",
                        "fact_ids": [f["fact_id"]],
                        "note": "similarly named organisation",
                    }
                )
        for facts in by_attr.values():
            if len({f["value"] for f in facts}) > 1:
                findings.append(
                    {
                        "kind": "contradictory_fact",
                        "fact_ids": [f["fact_id"] for f in facts],
                        "note": "values disagree",
                    }
                )
        return {"findings": findings, "proposals": list(world.proposals)}

    return run


def honest_distill(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """One claim per fact (first three), tied to its fact id."""
    facts = payload["facts"][:3]
    return {
        "hypotheses": [
            {
                "text": f"The account may be prioritising {f['attribute']}.",
                "fact_ids": [f["fact_id"]],
            }
            for f in facts
        ],
        "questions": [
            {"text": f"What is driving the {f['attribute']} work?", "fact_ids": [f["fact_id"]]}
            for f in facts
        ],
        "narrative": [
            {
                "text": "Recent activity points to a platform investment.",
                "fact_ids": [f["fact_id"] for f in facts],
            }
        ]
        if facts
        else [],
    }


def honest_setup(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The first visited page whose URL looks like a careers page."""
    page = next((p for p in payload["visited_pages"] if "careers" in p["url"]), None)
    return {"careers_page_id": page["page_id"] if page else None}


class FakeLlm:
    """See the module docstring."""

    def __init__(self, scripts: Mapping[str, Script], tokens: int = 100) -> None:
        self.scripts = dict(scripts)
        self.tokens = tokens
        self.calls: Counter[str] = Counter()
        self.prompts: list[str] = []

    async def call(
        self,
        task_kind: str,
        prompt_text: str,
        prompt_hash: str,
        payload: Mapping[str, Any],
        output: type[Any],
        ctx: RunContext,
    ) -> tuple[Any, int]:
        self.calls[task_kind] += 1
        self.prompts.append(prompt_text)
        return output.model_validate(dict(self.scripts[task_kind](payload))), self.tokens


def honest_llm(cfg: SkillConfig, world: World) -> FakeLlm:
    """A FakeLlm whose every script is honest."""
    return FakeLlm(
        {
            "abm_setup": honest_setup,
            "abm_pass1_extract": honest_extract(cfg),
            "abm_pass2_extract": honest_extract(cfg),
            "abm_people_extract": honest_people,
            "abm_sense_check": honest_sense(world),
            "abm_distill": honest_distill,
        }
    )


__all__ = ["InternalHistorySettings"]
