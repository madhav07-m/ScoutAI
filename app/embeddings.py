"""
Phase 3 — Embeddings.

Model: sentence-transformers/all-MiniLM-L6-v2, served via `fastembed`
instead of the `sentence-transformers` package.

Why fastembed and not sentence-transformers: `sentence-transformers`
pulls in full PyTorch as a hard dependency, which alone can add
several hundred MB of resident memory once the model is loaded --
fine locally, but enough to push a 512MB-limited deployment (e.g.
Render's free tier) into OOM. `fastembed` runs the same MiniLM model
through ONNX Runtime instead of PyTorch, which has a much smaller
memory footprint for CPU inference on a small model like this one, at
effectively the same embedding quality (same underlying weights).

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
from fastembed import TextEmbedding

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    """Load the embedding model once and cache it (loading is slow).

    Stays lazy exactly like before: this only runs on the first call
    to embed_texts(), not at module import time, so process startup /
    port-binding isn't blocked on model load.
    """
    return TextEmbedding(model_name=MODEL_NAME)


def embed_texts(texts: List[str]) -> np.ndarray:
    """Embed a list of strings into a (N, 384) numpy array of vectors.

    fastembed's TextEmbedding.embed() already L2-normalizes output for
    this model, matching the normalize_embeddings=True behavior the
    previous sentence-transformers call used -- so cosine-distance
    scoring in vector_store.py needs no changes.
    """
    model = get_model()
    embeddings = list(model.embed(texts))
    return np.array(embeddings)