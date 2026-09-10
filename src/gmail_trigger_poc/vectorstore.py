"""ChromaDB product vector store + the embedding function behind it.

Plan B keeps vectors in ChromaDB (`PersistentClient(./chroma_data)`, collection
`products`, Chroma id == product id as a string). We compute embeddings
ourselves and pass them to `upsert`/`query` explicitly, so the persisted
collection carries no embedding-function config to reconstruct on reopen.

Embedders:
  - DefaultEmbedder : Chroma's bundled ONNX all-MiniLM-L6-v2 (real semantics,
    ~80 MB model downloaded once and cached under ~/.cache/chroma).
  - HashEmbedder    : deterministic hashed bag-of-words, pure-Python, offline,
    no model. Cosine over these approximates token overlap. Used by tests and
    as the automatic fallback when the MiniLM model can't be loaded.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol

from gmail_trigger_poc.config import Settings, get_settings

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_HASH_DIM = 512

PRODUCTS_COLLECTION = "products"


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Deterministic hashed bag-of-words -> L2-normalized fixed-dim vector."""

    name = "hash"

    def __init__(self, dim: int = _HASH_DIM) -> None:
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN.findall((text or "").lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class DefaultEmbedder:
    """Chroma's bundled ONNX MiniLM embedding function (lazy-loaded)."""

    name = "chroma_default"

    def __init__(self) -> None:
        from chromadb.utils import embedding_functions

        self._ef = embedding_functions.DefaultEmbeddingFunction()

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = self._ef(texts)
        return [list(map(float, v)) for v in out]


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    choice = settings.embedding_provider or "auto"
    if choice == "hash":
        logger.info("embeddings: HashEmbedder (deterministic, offline)")
        return HashEmbedder()
    if choice in {"auto", "chroma_default"}:
        try:
            emb = DefaultEmbedder()
            logger.info("embeddings: Chroma DefaultEmbedder (ONNX all-MiniLM-L6-v2)")
            return emb
        except Exception:  # noqa: BLE001 - any load/download failure -> deterministic fallback
            if choice == "chroma_default":
                raise
            logger.warning(
                "embeddings: MiniLM unavailable -> falling back to HashEmbedder", exc_info=True
            )
            return HashEmbedder()
    logger.warning("unknown EMBEDDING_PROVIDER=%r -> HashEmbedder", choice)
    return HashEmbedder()


def get_client(settings: Settings | None = None):
    settings = settings or get_settings()
    import chromadb

    return chromadb.PersistentClient(path=settings.chroma_path)


def get_products_collection(settings: Settings | None = None, client=None):
    """The `products` collection (cosine space). Embeddings are supplied by caller."""
    settings = settings or get_settings()
    client = client or get_client(settings)
    return client.get_or_create_collection(
        PRODUCTS_COLLECTION, metadata={"hnsw:space": "cosine"}
    )
