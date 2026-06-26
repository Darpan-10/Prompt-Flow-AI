"""
Module 5 – Embedding Service
Lazy-loaded singleton for generating query embeddings.

Module 4 stores pre-computed embeddings in PostgreSQL (for papers).
Module 5 generates embeddings at query time (for search queries) using the
SAME model (all-mpnet-base-v2) to ensure dimensional match (768-dim).
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
            if _model is None:
                log.info("Loading sentence-transformers model: %s ...", MODEL_NAME)
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(MODEL_NAME)
                log.info("Model loaded. Embedding dimension: %d", EMBEDDING_DIM)
    return _model


def encode_query(query: str) -> list[float]:
    """
    Generate a 768-dim embedding from a search query.
    
    Matches the exact behavior of Module 4's embedding generation
    so query and document embeddings are comparable via cosine similarity.
    """
    model = _get_model()
    vector = model.encode(query).tolist()

    if len(vector) != EMBEDDING_DIM:
        raise ValueError(
            f"Embedding model returned {len(vector)} dims, expected {EMBEDDING_DIM}. "
            f"Did the model change? Check MODEL_NAME={MODEL_NAME}."
        )
    return vector


def is_model_loaded() -> bool:
    """Check if the embedding model has been loaded into memory yet."""
    return _model is not None


def warm_up() -> None:
    """
    Force model load at process startup rather than on first query.
    Call this once when the search service boots so the first real query
    doesn't pay the cold-start cost mid-request.
    """
    _get_model()
    # Run one throwaway encode to fully initialize internal tensors
    encode_query("warmup test query")
    log.info("Embedding service warmed up and ready.")
