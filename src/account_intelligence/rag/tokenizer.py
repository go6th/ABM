"""The one tokenizer shared by chunking (token budgets) and retrieval (BM25 text, query terms).

``tokenize`` is the regex ``[a-z0-9]+(?:[.-][a-z0-9]+)*`` over the casefolded text: punctuation
never sticks to a word (``"(Salesforce,"`` is ``salesforce``), a slash separates words
(``Veeva/Salesforce``), and a date or a version stays one token (``2026-03-01``, ``v2.1``).
The query side and the index side agree by construction because both call this function.

``Calibration`` counts the tokens a passage costs a model (brief B4): a real tokenizer when the
runtime has one (``tiktoken``), otherwise the configured ``chars_per_token`` is calibrated once
per file from a sample of its first lines (``calibrate``), so a Japanese transcript or a German
contract gets a budget that fits the model instead of the English estimate. The calibration is a
pure function of the sample, so the same file always gets the same passages.

GUARANTEE(test_the_shared_tokenizer_strips_punctuation_and_keeps_dates_whole)

GUARANTEE(test_the_token_budget_is_calibrated_once_per_file_from_its_own_text)
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

TOKEN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+(?:['.-][A-Za-z0-9]+)*")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_OTHER_LETTER = re.compile(r"[^\x00-\x7f\s]")
_PUNCT = re.compile(r"[^\w\s]")
SAMPLE_LINES = 20
MIN_SAMPLE_CHARS = 40  # less than this says nothing about the file: keep the configured value
MIN_CHARS_PER_TOKEN, MAX_CHARS_PER_TOKEN = 1.0, 6.0


def tokenize(text: str) -> tuple[str, ...]:
    """The casefolded word tokens of ``text`` (see the module docstring)."""
    return tuple(TOKEN.findall(text.casefold()))


@lru_cache(maxsize=1)
def real_counter() -> Callable[[str], int] | None:
    """A token counter from a real tokenizer when the runtime has one, else None."""
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - an encoding that cannot load is the same as none
        return None

    def count(text: str) -> int:
        """The tokenizer's token count of ``text`` (special tokens never allowed)."""
        return len(encoding.encode(text, disallowed_special=()))

    return count


def estimate_tokens(text: str) -> int:
    """A language-aware estimate without a tokenizer: an ASCII word is about 1.3 tokens, a CJK
    character one, another non-ASCII letter 0.4, a punctuation mark one."""
    ascii_words = len(_ASCII_WORD.findall(text))
    cjk = len(_CJK.findall(text))
    other = len(_OTHER_LETTER.findall(text)) - cjk
    punct = len(_PUNCT.findall(text))
    return max(1, math.ceil(ascii_words * 1.3 + cjk + other * 0.4 + punct))


@dataclass(frozen=True)
class Calibration:
    """How a file's token budget is counted: a real tokenizer, or chars per token."""

    chars_per_token: float
    source: str  # tokenizer | sample | config

    def tokens(self, text: str) -> int:
        """The budget tokens of ``text`` (0 for empty text)."""
        if not text:
            return 0
        counter = real_counter() if self.source == "tokenizer" else None
        if counter is not None:
            return max(1, counter(text))
        return math.ceil(len(text) / self.chars_per_token)


def calibrate(sample: str, default_chars_per_token: float) -> Calibration:
    """The calibration for a file from its first ``SAMPLE_LINES`` non-empty lines. With a real
    tokenizer the sample is not needed; with none, chars per token is the sample's ratio,
    clamped to [1, 6]; an empty sample keeps the configured value."""
    if real_counter() is not None:
        return Calibration(default_chars_per_token, "tokenizer")
    lines = [ln for ln in sample.splitlines() if ln.strip()][:SAMPLE_LINES]
    text = "\n".join(lines)
    if len(text) < MIN_SAMPLE_CHARS:
        return Calibration(default_chars_per_token, "config")
    ratio = len(text) / estimate_tokens(text)
    clamped = min(MAX_CHARS_PER_TOKEN, max(MIN_CHARS_PER_TOKEN, round(ratio, 3)))
    return Calibration(clamped, "sample")
