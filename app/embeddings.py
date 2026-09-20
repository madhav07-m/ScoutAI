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
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import List

import numpy as np
from fastembed import TextEmbedding

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Explicit, stable cache location instead of fastembed/huggingface_hub's
# default (which lands in the OS temp dir -- on Windows that's
# C:\Users\<you>\AppData\Local\Temp\fastembed_cache). Two problems with
# the default: (1) Temp can get cleared by cleanup tools or reboots, so
# the "cache" isn't reliably persistent, and (2) huggingface_hub tries
# to symlink the cached blob into place on every load, which requires
# either Windows Developer Mode or admin privileges -- without either,
# that symlink step fails with WinError 1314 and silently falls back to
# re-downloading the ~90MB model over HTTP almost every restart, which
# is most of what was making backend restarts slow.
#
# Moving the cache under the project directory (overridable via
# FASTEMBED_CACHE_DIR) sidesteps both: it's a normal, persistent folder,
# and as long as Developer Mode is also enabled (fixes the symlink
# privilege issue itself), the model downloads once and every
# subsequent load is near-instant.
_DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "fastembed"
CACHE_DIR = os.environ.get("FASTEMBED_CACHE_DIR", str(_DEFAULT_CACHE_DIR))
Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    """Load the embedding model once and cache it (loading is slow).
    Stays lazy: only runs on the first call to embed_texts(), not at
    module import time, so process startup / port-binding isn't
    blocked on model load."""
    return TextEmbedding(model_name=MODEL_NAME, cache_dir=CACHE_DIR)


def embed_texts(texts: List[str]) -> np.ndarray:
    """Embed a list of strings into a (N, 384) numpy array of vectors.
    fastembed's TextEmbedding.embed() already L2-normalizes output for
    this model, matching sentence-transformers' normalize_embeddings=True."""
    model = get_model()
    embeddings = list(model.embed(texts))
    return np.array(embeddings)