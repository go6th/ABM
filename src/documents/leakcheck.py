"""The leak check: does a client-facing text carry the tenant's private uploaded material?

Two tests over the account's private passages (``document_passages`` and the ``chunks`` index):

* **Shared word runs.** Every run of ``ngram_words`` consecutive words (lowercased, hashed) of a
  private passage is compared with the client text; runs that also appear in the account's
  public material (evidence sentences and the values of public facts: what the files render
  from public sources) are not counted (public text quoted in a private document is still
  public). More than ``max_ngram_hits`` shared runs fails.
* **Embedding similarity.** Each client paragraph of at least ``min_paragraph_words`` words is
  embedded and compared with the private index; a cosine similarity at or above
  ``embedding_threshold`` fails (a near copy with changed words).

Only fingerprints, scores and document ids are recorded, never text: every hit names the
uploaded document it matched, so an operator can see which file held a delivery back. An
account without private passages passes at once. The product's own earlier deliverables are not
private passages (``documents.repo.private_passages``).

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

    @property
    def documents(self) -> list[str]:
        """The ids of the uploaded documents the hits matched (sorted, each once)."""
        return sorted({str(h["document"]) for h in self.hits if h.get("document")})


class LeakChecker:
    """Built once per run from the account's private passages (``(document id, text)`` pairs, or
    bare texts) and its public material."""

    def __init__(
        self,
        cfg: LeakCfg,
        embedder: EmbeddingPort,
        private_texts: Iterable[str | tuple[Any, str]],
        public_texts: Iterable[str],
        nearest: Any,
    ):
        self.cfg = cfg
        self.embedder = embedder
        # async (vector) -> the best similarity in the private index, alone or with the id of
        # the document that holds it
        self.nearest = nearest
        public: set[int] = set()
        for t in public_texts:
            public |= shingles(t, cfg.ngram_words)
        self.private: dict[int, str | None] = {}  # word run -> the first document carrying it
        self.count = 0
        for item in private_texts:
            doc, t = (None, item) if isinstance(item, str) else (str(item[0]), item[1])
            self.count += 1
            for h in shingles(t, cfg.ngram_words):
                self.private.setdefault(h, doc)
        for h in public:
            self.private.pop(h, None)

    async def check(self, target: str, text: str) -> LeakResult:
        """See the module docstring."""
        result = LeakResult(target, passages_checked=self.count)
        if not self.count:
            return result
        shared = shingles(text, self.cfg.ngram_words) & self.private.keys()
        if len(shared) > self.cfg.max_ngram_hits:
            result.ngram_hits = len(shared)
            for h in sorted(shared)[:20]:
                hit: dict[str, Any] = {"kind": "ngram", "fingerprint": f"{h:016x}"}
                if self.private[h] is not None:
                    hit["document"] = self.private[h]
                result.hits.append(hit)
        paras = paragraphs(text, self.cfg.min_paragraph_words)
        if paras:
            vectors = await self.embedder.embed(paras)
            for index, vec in enumerate(vectors):
                found = await self.nearest(vec)
                sim, doc = found if isinstance(found, tuple) else (found, None)
                sim = float(sim)
                result.max_similarity = max(result.max_similarity, sim)
                if sim >= self.cfg.embedding_threshold:
                    result.embedding_hits += 1
                    digest = hashlib.sha256(paras[index].encode()).hexdigest()[:16]
                    hit = {"kind": "embedding", "paragraph": digest, "similarity": round(sim, 4)}
                    if doc is not None:
                        hit["document"] = str(doc)
                    result.hits.append(hit)
        return result
