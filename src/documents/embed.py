"""Passage embeddings: a port and the local deterministic embedder used until a vendor is chosen.

``HashingEmbedder`` (model ``local-hash-v1``) maps lowercase word unigrams and bigrams and
character trigrams into the index's 1536 dimensions with a stable hash and a sign bit, then
normalises to unit length. It runs in process, costs nothing, sends nothing anywhere and gives
the same vector for the same text on every machine. It measures shared wording, not meaning: it
is enough for the leak check's near-duplicate test and for hybrid retrieval next to full text,
not a semantic model (the vendor choice is an open decision). Every batch is metered with its
model and cost through ``FileModels``' call log (cost 0 here).

GUARANTEE(test_hashing_embedder_is_deterministic_and_normalised)
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

_WORD = re.compile(r"[a-z0-9]+(?:['.-][a-z0-9]+)*")


class EmbeddingPort(Protocol):
    """Vectors for texts, with the model name and the cost of a batch."""

    model: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One unit vector per text, in order."""
        ...

    def cost_usd(self, texts: Sequence[str]) -> float:
        """What embedding ``texts`` costs."""
        ...


def _bucket(feature: str, dims: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dims, (1.0 if value >> 63 else -1.0)


def features(text: str) -> dict[str, float]:
    """Weighted features of ``text`` (see the module docstring)."""
    words = _WORD.findall(text.lower())
    out: dict[str, float] = {}
    for w in words:
        out[f"w:{w}"] = out.get(f"w:{w}", 0.0) + 1.0
        if len(w) > 3:
            for i in range(len(w) - 2):
                key = f"c:{w[i : i + 3]}"
                out[key] = out.get(key, 0.0) + 0.25
    for a, b in zip(words, words[1:], strict=False):
        key = f"b:{a} {b}"
        out[key] = out.get(key, 0.0) + 1.5
    return out


class HashingEmbedder:
    """See the module docstring."""

    def __init__(self, dimensions: int = 1536, model: str = "local-hash-v1"):
        self.dimensions = dimensions
        self.model = model

    def vector(self, text: str) -> list[float]:
        """The unit vector of one text (all zeros for text without words)."""
        vec = [0.0] * self.dimensions
        for feature, weight in features(text).items():
            index, sign = _bucket(feature, self.dimensions)
            vec[index] += sign * (1.0 + math.log(weight))
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """See ``EmbeddingPort``."""
        return [self.vector(t) for t in texts]

    def cost_usd(self, texts: Sequence[str]) -> float:
        """Nothing: it runs in process."""
        del texts
        return 0.0


def literal(vec: Sequence[float]) -> str:
    """The pgvector text form of a vector (``[0.1,0.2,...]``), 6 decimals."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


SEMANTIC_DIMENSIONS = 384


def _providers(engine: Any) -> list[str]:
    """The onnxruntime execution providers a loaded fastembed engine ended up with."""
    session = getattr(getattr(engine, "model", None), "model", None)
    return list(session.get_providers()) if session is not None else []


class FastEmbedder:
    """Meaning-based vectors from a small transformer run on the server itself (owner decision
    2026-10-15: on-server, no per-use cost, no private text leaves the system): BAAI
    bge-small-en-v1.5 through fastembed (ONNX), 384 dimensions, unit length. Passages and
    questions are embedded with the model's own passage and query prompts.

    Where the process has a GPU (the worker image carries ``onnxruntime-gpu`` and Cloud Run
    attaches an NVIDIA L4, 2026-10-16) the model runs on it through CUDA, with batches of 256;
    otherwise on the CPU, one thread per core, batches of 32. The choice is made once at the
    first call and logged (``documents.embedder.loaded``); a CUDA runtime that fails to start
    falls back to the CPU rather than failing the index.

    It is used for retrieval only (``chunks.semantic_embedding``); the leak gate keeps the
    hashing vectors its threshold was tuned for. The model files are baked into the image
    (``FASTEMBED_CACHE_PATH``), so nothing is downloaded at run time."""

    CPU_BATCH = 32
    GPU_BATCH = 256

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None):
        import os

        self.model_name = model_name
        self.model = model_name.split("/")[-1]
        self.dimensions = SEMANTIC_DIMENSIONS
        self.cache_dir = cache_dir or os.environ.get("FASTEMBED_CACHE_PATH") or None
        self._engine: Any = None
        self.device = "unloaded"
        self.batch_size = self.CPU_BATCH

    def _load(self) -> Any:
        if self._engine is None:
            import logging
            import os

            import onnxruntime as ort  # type: ignore[import-untyped]
            from fastembed import TextEmbedding

            log = logging.getLogger("documents.embedder")
            # one thread per core up to eight (the worker has eight since 2026-10-16; before, two
            # threads indexed a 37,000-passage file at half the speed the instance could give)
            threads = max(1, min(os.cpu_count() or 2, 8))
            engine = None
            if "CUDAExecutionProvider" in ort.get_available_providers():
                try:
                    engine = TextEmbedding(self.model_name, cache_dir=self.cache_dir, cuda=True)
                    if "CUDAExecutionProvider" not in _providers(engine):
                        engine = None  # the runtime loaded but CUDA did not come up (warning above)
                except Exception as exc:  # no driver or CUDA libraries on this instance
                    log.warning(
                        "documents.embedder.cuda_unavailable",
                        extra={"error_class": type(exc).__name__},
                    )
                    engine = None
            if engine is None:
                engine = TextEmbedding(
                    self.model_name, cache_dir=self.cache_dir, threads=threads, cuda=False
                )
                self.device, self.batch_size = "cpu", self.CPU_BATCH
            else:
                self.device, self.batch_size = "cuda", self.GPU_BATCH
            self._engine = engine
            # the message is the event name and the fields are catalogued (config/telemetry/
            # logs.yaml): kind is the device, count the CPU threads
            log.info(
                "documents.embedder.loaded",
                extra={"model": self.model, "kind": self.device, "count": threads},
            )
        return self._engine

    def _passages(self, texts: list[str]) -> list[list[float]]:
        vectors = self._load().passage_embed(texts, batch_size=self.batch_size)
        return [[float(x) for x in v] for v in vectors]

    def _queries(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._load().query_embed(texts)]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Passage vectors, in order (CPU work runs off the event loop)."""
        import asyncio

        return await asyncio.to_thread(self._passages, list(texts))

    async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """Question vectors, in order."""
        import asyncio

        return await asyncio.to_thread(self._queries, list(texts))

    def cost_usd(self, texts: Sequence[str]) -> float:
        """Nothing per call: it runs in process."""
        del texts
        return 0.0
