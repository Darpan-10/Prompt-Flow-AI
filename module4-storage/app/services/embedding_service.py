"""
Module 4 – Embedding Service
Generates 768-dim embeddings locally using sentence-transformers/all-mpnet-base-v2.

Per the locked design: Module 3 always sends embedding=null. Module 4's
consumer calls generate_embedding() before inserting into PostgreSQL,
but ONLY when the paper will be PUBLISHED (cost optimization — we don't
waste GPU/CPU cycles embedding papers that are going to PENDING_REVIEW
or REJECTED).

The model is loaded once per process (lazy singleton) since
SentenceTransformer instantiation is expensive (~1-2s) and the model
itself is ~420MB in memory.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()

MODEL_NAME = "all-mpnet-base-v2"
EMBEDDING_DIM = 768


def _get_model():
    """
    Lazy-load the SentenceTransformer model (thread-safe singleton).
    First call pays the ~1-2s load cost; subsequent calls reuse it.
    """
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:  # double-checked locking
                log.info("Loading sentence-transformers model: %s ...", MODEL_NAME)
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME)
                log.info("Model loaded. Embedding dimension: %d", EMBEDDING_DIM)
    return _model


def generate_embedding(title: str, venue: Optional[str] = None) -> list[float]:
    """
    Generate a 768-dim embedding from paper title (+ venue if present).

    Matches the exact locked spec:
        text = title
        if venue:
            text += f" {venue}"
        return embedding_model.encode(text).tolist()
    """
    model = _get_model()
    text = title
    if venue:
        text = f"{text} {venue}"
    vector = model.encode(text).tolist()

    if len(vector) != EMBEDDING_DIM:
        # Should never happen with all-mpnet-base-v2, but fail loudly if the
        # model is ever swapped for one with a different output dimension.
        raise ValueError(
            f"Embedding model returned {len(vector)} dims, expected {EMBEDDING_DIM}. "
            f"Did the model change? Check MODEL_NAME={MODEL_NAME}."
        )
    return vector


def warm_up() -> None:
    """
    Force model load at process startup rather than on first message.
    Call this once when the consumer boots so the first real paper
    doesn't pay the cold-start cost mid-transaction.
    """
    _get_model()
    # Run one throwaway encode to fully initialize internal tensors
    generate_embedding("warmup", "warmup venue")
    log.info("Embedding service warmed up and ready.")
