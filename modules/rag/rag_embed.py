"""
Text embedders for the RAG layer.

Two backends, one interface (`embed(text) -> list[float]`):

  HashingEmbedder            Pure-Python feature hashing. No dependencies, no
                             downloads, deterministic. Similarity is lexical
                             (shared words) — a solid offline baseline that also
                             makes the retrieval pipeline testable anywhere.

  SentenceTransformerEmbedder  Real semantic embeddings via sentence-transformers,
                             if it is installed. Understands paraphrase, not just
                             word overlap.

get_embedder() returns the best available backend, or honors JOB_BOT_EMBEDDER
("hashing" | "sentence-transformers").
"""

from __future__ import annotations

import hashlib
import math
import os
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class HashingEmbedder:
    """Signed feature hashing into a fixed-dimension, L2-normalized vector."""

    name = "hashing"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _bucket(self, token: str) -> tuple[int, float]:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % self.dim
        sign = 1.0 if digest[4] & 1 else -1.0
        return idx, sign

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _tokenize(text):
            idx, sign = self._bucket(token)
            vec[idx] += sign
        return _l2_normalize(vec)


class SentenceTransformerEmbedder:
    """Semantic embeddings via sentence-transformers (optional dependency)."""

    name = "sentence-transformers"

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # lazy, optional

        self._model = SentenceTransformer(model)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        vec = self._model.encode(text or "", normalize_embeddings=True)
        return [float(x) for x in vec]


def get_embedder(dim: int = 256):
    """
    Pick an embedder. Defaults to hashing (zero-dependency, deterministic); set
    JOB_BOT_EMBEDDER=sentence-transformers to use semantic embeddings if the
    package is installed. Falls back to hashing if that import fails.
    """
    choice = os.environ.get("JOB_BOT_EMBEDDER", "hashing").strip().lower()
    if choice in ("sentence-transformers", "st", "semantic"):
        try:
            return SentenceTransformerEmbedder()
        except Exception:
            # Package missing or model download blocked — degrade gracefully.
            return HashingEmbedder(dim=dim)
    return HashingEmbedder(dim=dim)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Inputs from the embedders are already L2-normalized,
    so this is a dot product, but we normalize defensively for mixed sources."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
