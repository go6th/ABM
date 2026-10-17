"""The shared tokenizer and the per-file budget calibration (rag/tokenizer.py, brief B4): one
regex for the index side and the query side, and a budget that fits the file's own language —
calibrated from the file's first lines when no real tokenizer is installed, a pure function of
the sample so the same file always parts the same way.
"""

from __future__ import annotations

from documents.chunking import Chunker
from documents.config import load_config
from documents.redact import Redactor
from documents.sandbox import ParsedUnit

CFG = load_config()


def test_the_shared_tokenizer_strips_punctuation_and_keeps_dates_whole() -> None:
    """The index side and the query side call the same function: punctuation never sticks to a
    word, a slash separates, and dates and versions stay one token."""
    from account_intelligence.rag import reflection, retrieval
    from account_intelligence.rag.tokenizer import tokenize

    assert retrieval.tokenize is tokenize  # retrieval imports the shared one…
    assert reflection.tokenize is tokenize  # …and reflection re-exports retrieval's
    assert tokenize('"(Salesforce," said Veeva/Salesforce on 2026-03-01: v2.1 shipped.') == (
        "salesforce",
        "said",
        "veeva",
        "salesforce",
        "on",
        "2026-03-01",
        "v2.1",
        "shipped",
    )
    assert tokenize("") == () and tokenize("!!!") == ()


def test_the_token_budget_is_calibrated_once_per_file_from_its_own_text() -> None:
    """calibrate: an English sample lands near the default ratio; a CJK sample near one char per
    token; a tiny sample keeps the config; the chunker then budgets with it, deterministically —
    the same file parts into the same passages on a re-read."""
    from account_intelligence.rag.tokenizer import calibrate

    english = " ".join(f"Sentence {i} explains the rollout plan in detail." for i in range(40))
    default = float(CFG.chunking.chars_per_token)
    eng = calibrate(english, default)
    if eng.source == "tokenizer":
        # a real tokenizer present: the sample is not needed and the budget is the tokenizer's
        assert eng.chars_per_token == default
    else:
        assert eng.source == "sample" and abs(eng.chars_per_token - default) < default
    japanese = "これはロールアウト計画の説明です。" * 30
    jp = calibrate(japanese, default)
    assert jp.source in ("sample", "tokenizer")
    if jp.source == "sample":
        assert jp.chars_per_token <= 1.5  # a CJK file costs roughly a token per character
    assert calibrate("", default).source == "config"
    assert calibrate("tiny", default).source == "config"
    clamped = calibrate("word " * 400, 0.1)
    if clamped.source == "sample":
        assert 1.0 <= clamped.chars_per_token <= 6.0

    def chunk(text: str) -> list[tuple[str, str]]:
        c = Chunker(CFG.chunking, "call.txt", Redactor("standard"), 100)
        unit = ParsedUnit("section", {"section": "(no heading)"}, text)
        return [(p.text, p.sha256) for p in c.passages([unit])]

    long_japanese = japanese * 200  # far past any passage cap worth the name
    first, again = chunk(long_japanese), chunk(long_japanese)
    assert first == again and len(first) > len(chunk("x " * 10))
    if calibrate(japanese, default).source == "sample":
        flat = Chunker(CFG.chunking, "call.txt", Redactor("standard"), 100)
        flat._cal = calibrate("", default)  # as if never calibrated: the English estimate
        unit = ParsedUnit("section", {"section": "(no heading)"}, long_japanese)
        flat_out = [(p.text, p.sha256) for p in flat.passages([unit])]
        assert len(first) > len(flat_out)  # calibrated: the file's real token cost parts it finer
