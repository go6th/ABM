"""Union-find merge plan with corroboration rules (P3). Pure.

Rules, all symmetric and decided over the whole batch, so the plan does not depend on input order:

1. Records with the same canonical profile URL are one person (``merge.url``).
2. A record WITHOUT a URL joins a URL group only when its normalised name equals a member's name
   AND at least one corroborating signal agrees (same employer domain, a shared lane, or similar
   title), and exactly one URL group qualifies (``merge.name``). Equal names with no corroboration,
   or with several qualifying URL groups, flag ``possible_duplicate`` and do not merge.
3. Records without a URL that qualify for no URL group merge with each other under the same name
   rule; equal names without corroboration flag both.
4. Two different canonical URLs are never merged; equal names across them flag both.

The canonical member of a group is the one with a URL and the smallest key (any member when none
has a URL). Every other member points at it; the canonical member points at nothing, so there is
no chain and no cycle. ``unmerge`` reverses one merge and records the event.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from people.errors import MergeError
from people.names import titles_similar


@dataclass(frozen=True)
class MergeItem:
    """What the merge rules read from one found record."""

    key: str
    url: str | None
    name: str  # normalised ('' when absent)
    employer_domain: str | None = None
    lanes: frozenset[str] = frozenset()
    title_tokens: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MergeEvent:
    """One merge or unmerge, with the rule that decided it."""

    kind: str  # merge | unmerge
    key: str
    into: str | None
    rule_id: str


@dataclass(frozen=True)
class MergePlan:
    """``canonical_of`` maps every key to its canonical key (itself when canonical)."""

    canonical_of: dict[str, str]
    possible_duplicates: frozenset[str]
    events: tuple[MergeEvent, ...] = field(default=())

    def merged_into(self, key: str) -> str | None:
        """The canonical key a merged record points at, ``None`` for a canonical record."""
        target = self.canonical_of[key]
        return None if target == key else target

    @property
    def merged_away(self) -> int:
        """Records that point at another record."""
        return sum(1 for k, v in self.canonical_of.items() if k != v)


class _UnionFind:
    def __init__(self, keys: Iterable[str]) -> None:
        self.parent = {k: k for k in keys}

    def find(self, key: str) -> str:
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:  # path compression
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            low, high = sorted((ra, rb))
            self.parent[high] = low


def corroborates(a: MergeItem, b: MergeItem, threshold: float) -> bool:
    """At least one independent signal agrees: employer domain, a lane, or a similar title."""
    if a.employer_domain and a.employer_domain == b.employer_domain:
        return True
    if a.lanes & b.lanes:
        return True
    return titles_similar(a.title_tokens, b.title_tokens, threshold)


def plan_merge(items: Sequence[MergeItem], threshold: float) -> MergePlan:
    """The merge plan for one batch (see the module docstring)."""
    keys = [i.key for i in items]
    if len(set(keys)) != len(keys):
        raise MergeError("duplicate record keys in one batch")
    uf = _UnionFind(keys)
    flagged: set[str] = set()
    by_url: dict[str, list[MergeItem]] = {}
    for item in items:
        if item.url:
            by_url.setdefault(item.url, []).append(item)
    for members in by_url.values():
        for other in members[1:]:
            uf.union(members[0].key, other.key)
    urls = sorted(by_url)
    for i, left in enumerate(urls):  # rule 4: equal names across different URLs
        for right in urls[i + 1 :]:
            for a in by_url[left]:
                for b in by_url[right]:
                    if a.name and a.name == b.name:
                        flagged.update((a.key, b.key))
    pending: list[MergeItem] = []
    for item in (i for i in items if not i.url):
        if not item.name:
            continue
        same_name = [v for v in items if v.url and v.name == item.name]
        qualifying = {v.url for v in same_name if v.url and corroborates(item, v, threshold)}
        if len(qualifying) == 1:
            uf.union(item.key, by_url[next(iter(qualifying))][0].key)
        elif qualifying or same_name:
            flagged.add(item.key)
        else:
            pending.append(item)
    for i, a in enumerate(pending):
        for b in pending[i + 1 :]:
            if a.name != b.name:
                continue
            if corroborates(a, b, threshold):
                uf.union(a.key, b.key)
            else:
                flagged.update((a.key, b.key))
    groups: dict[str, list[MergeItem]] = {}
    for item in items:
        groups.setdefault(uf.find(item.key), []).append(item)
    canonical_of: dict[str, str] = {}
    events: list[MergeEvent] = []
    for members in groups.values():
        with_url = sorted((m for m in members if m.url), key=lambda m: m.key)
        head = with_url[0] if with_url else min(members, key=lambda m: m.key)
        for m in sorted(members, key=lambda m: m.key):
            canonical_of[m.key] = head.key
            if m.key != head.key:
                rule = "merge.url" if m.url and m.url == head.url else "merge.name"
                events.append(MergeEvent("merge", m.key, head.key, rule))
    merged = {k for k, v in canonical_of.items() if k != v}
    plan = MergePlan(
        canonical_of, frozenset(flagged - merged), tuple(sorted(events, key=lambda e: e.key))
    )
    check_plan(plan)
    return plan


def check_plan(plan: MergePlan) -> None:
    """Every pointer targets a canonical record (no chain, no cycle); ``MergeError`` otherwise."""
    for key, target in plan.canonical_of.items():
        if target not in plan.canonical_of:
            raise MergeError(f"{key} points at an unknown record")
        if plan.canonical_of[target] != target:
            raise MergeError(f"{key} points at a merged record")


def stable_plan(items: Sequence[MergeItem], threshold: float, cap: int) -> MergePlan:
    """The plan, recomputed up to ``cap`` times (forward and reversed order) and required to be
    identical each time (merge fixpoint check); ``MergeError`` when it is not."""
    plan = plan_merge(items, threshold)
    # LOOP(merge fixpoint check): cap=merge_fixpoint_cap; exit=identical plan, MergeError
    for attempt in range(max(cap - 1, 1)):
        order = list(reversed(items)) if attempt % 2 == 0 else list(items)
        again = plan_merge(order, threshold)
        if (again.canonical_of, again.possible_duplicates) != (
            plan.canonical_of,
            plan.possible_duplicates,
        ):
            raise MergeError("merge plan is not stable")
    return plan


def unmerge(plan: MergePlan, key: str) -> MergePlan:
    """Reverse the merge of ``key`` (it becomes canonical again); the event is recorded. A
    canonical record cannot be unmerged (``MergeError``)."""
    target = plan.merged_into(key)
    if target is None:
        raise MergeError(f"{key} is not merged")
    canonical_of = dict(plan.canonical_of)
    canonical_of[key] = key
    result = MergePlan(
        canonical_of,
        plan.possible_duplicates | {key},
        (*plan.events, MergeEvent("unmerge", key, None, "unmerge.manual")),
    )
    check_plan(result)
    return result
