"""A saved answer keeps the context size; the thread route must accept it (2026-11-05)."""

from __future__ import annotations

from account_intelligence.api import contract as C


def _answer(**extra: object) -> dict[str, object]:
    return {
        "paragraphs": [],
        "items": None,
        "sources": [],
        "used": {"workspace": "used", "web": "not_used", "mail_drive": "not_connected"},
        "offer": None,
        "followups": [],
        "account_domain": None,
        "account_name": None,
        "answered": True,
        **extra,
    }


def test_a_saved_answer_with_context_tokens_validates() -> None:
    got = C.ChatAnswer.model_validate(_answer(context_tokens=53038))
    assert got.context_tokens == 53038


def test_an_old_saved_answer_without_it_defaults_to_zero() -> None:
    assert C.ChatAnswer.model_validate(_answer()).context_tokens == 0


def test_a_saved_answer_the_contract_no_longer_fits_drops_its_cards_not_the_thread() -> None:
    # answer_json is written by the build that answered and read by every later one: a key the
    # api does not know made every GET of the thread a 500 (staging 2026-11-04)
    from account_intelligence.api.routes.chat import stored_answer

    assert stored_answer(None) is None
    kept = _answer(context_tokens=12)
    assert stored_answer(kept) is kept
    assert stored_answer(_answer(written_by_a_newer_build="x")) is None
    assert stored_answer(_answer(used={"workspace": "used"})) is None  # an older, shorter shape
