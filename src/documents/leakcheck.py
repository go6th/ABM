"""The leak check: does a client-facing text carry the tenant's private uploaded material?

Two tests over the account's private passages (``document_passages`` and the ``chunks`` index):

* **Shared word runs.** Every run of ``ngram_words`` consecutive words (lowercased, hashed) of a
  private passage is compared with the client text; runs that also appear in the run's public
  evidence are not counted (public text quoted in a private document is still public). More
  than ``max_ngram_hits`` shared runs fails.
* **Embedding similarity.** Each client paragraph of at least ``min_paragraph_words`` words is
  embedded and compared with the private index; a cosine similarity at or above
  ``embedding_threshold`` fails (a near copy with changed words).

Only fingerprints and scores are recorded, never text. An account without private passages
passes at once.

GUARANTEE(test_canary_from_a_private_file_blocks_client_release)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from documents.config import LeakCfg
from documents.embed import EmbeddingPort

_WORD = re.compile(r"[a-z0-9]+(?:['.][a-z0-9]+)*")
_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")


def _h(words: Sequence[str]) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(words).encode(), digest_size=8).digest(), "big")


def shingles(text: str, n: int) -> set[int]:
    """Hashed runs of ``n`` words (redaction placeholders removed first)."""
    words = _WORD.findall(_PLACEHOLDER.sub(" ", text).lower())
    return {_h(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}


def paragraphs(text: str, min_words: int) -> list[str]:
    """Paragraphs of at least ``min_words`` words."""
    parts = re.split(r"\n\s*\n|\n", text)
    return [p.strip() for p in parts if len(p.split()) >= min_words]


@dataclass
class LeakResult:
    """The verdict for one client-facing text."""

    target: str
    ngram_hits: int = 0
    embedding_hits: int = 0
    max_similarity: float = 0.0
    passages_checked: int = 0
    hits: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """pass or fail."""
        return "fail" if self.ngram_hits or self.embedding_hits else "pass"


class LeakChecker:
    """Built once per run from the account's private passages and the run's public evidence."""

    def __init__(
        self,
        cfg: LeakCfg,
        embedder: EmbeddingPort,
        private_texts: Iterable[str],
        public_texts: Iterable[str],
        nearest: Any,
    ):
        self.cfg = cfg
        self.embedder = embedder
        self.nearest = nearest  # async (vector) -> best similarity in the private index
        public: set[int] = set()
        for t in public_texts:
            public |= shingles(t, cfg.ngram_words)
        self.private: set[int] = set()
        self.count = 0
        for t in private_texts:
            self.count += 1
            self.private |= shingles(t, cfg.ngram_words)
        self.private -= public

    async def check(self, target: str, text: str) -> LeakResult:
        """See the module docstring."""
        result = LeakResult(target, passages_checked=self.count)
        if not self.count:
            return result
        shared = shingles(text, self.cfg.ngram_words) & self.private
        if len(shared) > self.cfg.max_ngram_hits:
            result.ngram_hits = len(shared)
            result.hits += [
                {"kind": "ngram", "fingerprint": f"{h:016x}"} for h in sorted(shared)[:20]
            ]
        paras = paragraphs(text, self.cfg.min_paragraph_words)
        if paras:
            vectors = await self.embedder.embed(paras)
            for index, vec in enumerate(vectors):
                sim = float(await self.nearest(vec))
                result.max_similarity = max(result.max_similarity, sim)
                if sim >= self.cfg.embedding_threshold:
                    result.embedding_hits += 1
                    digest = hashlib.sha256(paras[index].encode()).hexdigest()[:16]
                    result.hits.append(
                        {"kind": "embedding", "paragraph": digest, "similarity": round(sim, 4)}
                    )
        return result
