"""An extraction group too large for one model call is split, and an oversized payload is cut."""

from __future__ import annotations

from agents.config import load_skill_config
from agents.extraction import groups
from agents.schemas import PayloadView

CFG = load_skill_config()


def _view(ref: str, row: str, chars: int) -> PayloadView:
    return PayloadView(
        payload_ref=ref, row_key=row, url=f"https://a.example/{ref}", text="x" * chars
    )


def test_a_large_group_is_split_and_every_call_stays_under_the_limit() -> None:
    views = [_view(f"p{i}", "company_news", 40) for i in range(5)] + [
        _view("big", "company_news", 500)
    ]
    out = groups(CFG, views, "pass2", max_chars=100)
    news = [g for g in out if g.group == "structured_signals"]
    assert len(news) > 1
    assert all(sum(len(p.text) for p in g.payloads) <= 100 for g in news)
    assert sorted(p.payload_ref for g in news for p in g.payloads) == sorted(
        v.payload_ref for v in views
    )
    assert next(p for g in news for p in g.payloads if p.payload_ref == "big").text == "x" * 100


def test_a_small_group_stays_one_call() -> None:
    views = [_view(f"p{i}", "company_news", 10) for i in range(3)]
    assert len([g for g in groups(CFG, views, "pass2") if g.group == "structured_signals"]) == 1
