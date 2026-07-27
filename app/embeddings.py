"""
Phase 3 — Embeddings.

Model: sentence-transformers/all-MiniLM-L6-v2
- Fast, local, free, 384-dim vectors — good enough for semantic
  similarity on short resume/JD chunks, no API key or cost needed.

Why semantic embeddings beat keyword matching (be ready to explain
this in an interview): keyword/TF-IDF matching scores documents based
on shared surface tokens. "Python developer" and "software engineer
skilled in Python" share almost no keywords beyond "Python" itself, so
a keyword system under-scores the match. Embeddings map both phrases
to nearby points in vector space because the MODEL was trained to
capture meaning, not just token overlap — so semantically equivalent
phrasing scores similarly even with zero shared vocabulary. This is
exactly the failure mode recruiters run into with naive ATS keyword
filters, which is a good talking point for why this project exists.
"""

from functools import lru_cache
from typing import List
import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load the embedding model once and cache it (loading is slow)."""
    return SentenceTransformer(MODEL_NAME)


def embed_texts(texts: List[str]) -> np.ndarray:
    """Embed a list of strings into a (N, 384) numpy array of vectors."""
    model = get_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.array(embeddings)
