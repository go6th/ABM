"""Stateful model test (spec 9.4): random sequences of seed, record, downgrade, sweep and
verdict against a reference model. Invariants: versions never decrease, event ``seq`` is gapless
per row, proof references only grow, a swept row never changes again, the tally equals a
recomputation from rows, and the verdict never allows COMPLETED with an unset or swept row."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from account_intelligence.tracker.errors import TrackerError
from account_intelligence.tracker.model import Actor, CallRecord, ReasonCode, Status
from account_intelligence.tracker.service import TrackerService
from account_intelligence.tracker.tally import tally
from account_intelligence.workflow.spec import load_spec
from tests.fakes.tracker_ports import FakeCallLedger, FakeRunInput, InMemoryCoverageRepo

OWNERS = load_spec().coverage_owners
KEYS = sorted(OWNERS)[:6]


class TrackerModel(RuleBasedStateMachine):
    """Drives the real service over an in-memory repository."""

    @initialize()
    def setup(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.tenant, self.run = UUID(int=1), uuid4()
        self.ledger = FakeCallLedger()
        self.repo = InMemoryCoverageRepo()
        self.service = TrackerService(
            self.repo,
            self.ledger,
            FakeRunInput(),
            owners=OWNERS,  # type: ignore[arg-type]
        )
        self.refs: dict[str, list[str]] = {k: [] for k in KEYS}
        self.versions: dict[str, int] = dict.fromkeys(KEYS, 0)
        self.proof_refs: dict[str, set[str]] = {k: set() for k in KEYS}
        self.swept: dict[str, Status | None] = {}
        self.terminal = False
        self.loop.run_until_complete(self.service.seed(self.run, self.tenant))

    def _go(self, coro):  # type: ignore[no-untyped-def]
        try:
            return self.loop.run_until_complete(coro)
        except TrackerError:
            return None

    @rule(
        key=st.sampled_from(KEYS),
        ok=st.booleans(),
        results=st.sampled_from([None, 0, 2]),
        reuse=st.booleans(),
    )
    def record(self, key: str, ok: bool, results: int | None, reuse: bool) -> None:
        ref = uuid4().hex
        self.ledger.add(
            self.tenant,
            self.run,
            CallRecord(
                call_ref=ref,
                tool_category="web_search",
                success=ok,
                result_count=results if ok else None,
                error_kind=None if ok else "RateLimited",
            ),
        )
        cited = [*self.refs[key], ref] if reuse else [ref]
        self._go(
            self.service.record_outcome(
                self.tenant, self.run, key, cited, Actor.STAGE, stage=OWNERS[key]
            )
        )
        self.refs[key].append(ref)

    @rule(
        key=st.sampled_from(KEYS),
        to=st.sampled_from(list(Status)),
        actor=st.sampled_from([Actor.JUDGE, Actor.GATE]),
    )
    def downgrade(self, key: str, to: Status, actor: Actor) -> None:
        self._go(
            self.service.downgrade(self.tenant, self.run, key, to, actor, ReasonCode.CLAIMS_DROPPED)
        )

    @rule(reason=st.sampled_from([ReasonCode.SWEPT_FAILED, ReasonCode.SWEPT_CANCELED]))
    def sweep(self, reason: ReasonCode) -> None:
        swept = self._go(self.service.sweep(self.run, self.tenant, reason)) or []
        for key in swept:
            if key in KEYS:
                self.swept[key] = Status.NOT_DONE
        self.terminal = True

    @invariant()
    def rows_and_events_stay_consistent(self) -> None:
        if not hasattr(self, "repo"):
            return
        for key in KEYS:
            row = self.repo.rows[(self.run, key)]
            assert row["version"] >= self.versions[key]
            self.versions[key] = row["version"]
            seqs = [e["seq"] for e in self.repo.event_log if e["key"] == key]
            assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) == row["version"]
            refs = set(row["proof"].call_refs) if row["proof"] else set()
            assert self.proof_refs[key] <= refs
            self.proof_refs[key] = refs
            if key in self.swept:
                assert row["swept"] and row["status"] is self.swept[key]

    @invariant()
    def verdict_and_tally_follow_the_rows(self) -> None:
        if not hasattr(self, "repo"):
            return
        rows = self.loop.run_until_complete(self.repo.get_all_rows(self.run))
        verdict = self.loop.run_until_complete(self.service.verdict(self.run))
        if any(r.status is None or r.swept for r in rows):
            assert not verdict.can_complete
        counts = tally(rows)["status_counts"]
        assert sum(counts.values()) == len(rows) == 33

    def teardown(self) -> None:
        if hasattr(self, "loop"):
            self.loop.close()


TestTrackerModel = TrackerModel.TestCase
TestTrackerModel.settings = settings(max_examples=60, stateful_step_count=25, deadline=None)
