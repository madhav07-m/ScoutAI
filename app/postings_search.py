"""
Companies window — role/location search bar.

Design (per user preference): location is a FILTER, role/title is a
SEMANTIC search. Rationale spelled out for the person: pure semantic
search would let "Bangalore" fuzzily match "Bangkok" just because
they're both cities close-ish in embedding space, which is wrong for
a location filter — a person searching Bangalore wants Bangalore, not
"places kind of like Bangalore." Role/title benefits from semantic
matching for the same reason the resume ranker does: "ML engineer"
and "machine learning scientist" share little vocabulary but mean
similar things.

Location matching is substring + a curated metro-area alias table
(app/location_aliases.py) — e.g. searching "Bay Area" also returns
"San Francisco" postings, without embeddings and without the
Bangalore/Bangkok false-positive risk a full semantic approach would
reintroduce. See that module's docstring for the reasoning.

How the index works now (replaces the earlier ephemeral ChromaDB
collection):

  * The search index is one NumPy matrix (N x 384, float32, L2-normalised)
    plus a parallel list of metadata dicts. ~8 MB for 5,500 postings.
    Searching is `matrix @ query_vector` -- exact, and well under a
    millisecond at this size.
  * Each posting's embedding is SAVED in Postgres (posting_embeddings
    table, see companies_store.py), keyed by posting id + a hash of the
    embedded text. Building the index loads saved vectors and embeds
    only postings that are new or whose title/skills changed. So a
    restart or refresh costs seconds, not a full re-embed, and a crash
    mid-build loses nothing: batches are saved as they finish and the
    next build resumes where it stopped.
  * Nothing here uses chromadb, which removes both its ~60 MB import
    cost and the leak where every rebuilt collection stayed in memory.
"""

import gc
from hashlib import sha1
from typing import Callable, List, Optional

import numpy as np

from app.companies_store import load_posting_embeddings, save_posting_embeddings
from app.embeddings import embed_texts
from app.location_aliases import location_matches
from app.vector_store import normalize_rows

_EMBED_BATCH_SIZE = 64  # peak memory while embedding is bounded by this, not by posting count


class PostingsIndex:
    """In-memory postings index. Exposes count() so callers that used
    to call collection.count() keep working unchanged."""

    def __init__(self, vectors: np.ndarray, metas: List[dict]):
        self.vectors = vectors
        self.metas = metas

    def count(self) -> int:
        return len(self.metas)


def _posting_text(p: dict) -> str:
    """Title (+ skills, for a bit more signal) -- what gets embedded."""
    return p["title"] + (" " + " ".join(p["skills"]) if p.get("skills") else "")


def _text_hash(text: str) -> str:
    return sha1(text.encode("utf-8")).hexdigest()


def build_postings_collection(
    postings: List[dict],
    progress: Optional[Callable[[int, int], None]] = None,
) -> PostingsIndex:
    """Build the search index. Each posting dict must have id, company,
    title, location, url, salary_text, skills (see
    companies_store.get_all_postings).

    Saved embeddings are reused when the posting's text is unchanged;
    only the rest are embedded (in small batches, saved as they finish).
    progress(done, total_to_embed), if given, is called after each batch.
    """
    if not postings:
        return PostingsIndex(np.zeros((0, 0), dtype=np.float32), [])

    texts = [_posting_text(p) for p in postings]
    hashes = [_text_hash(t) for t in texts]
    ids = [p["id"] for p in postings]

    saved = load_posting_embeddings()
    vectors: List[Optional[np.ndarray]] = [None] * len(postings)
    to_embed: List[int] = []
    for i, (pid, h) in enumerate(zip(ids, hashes)):
        hit = saved.get(pid)
        if hit is not None and hit[0] == h:
            vectors[i] = hit[1]
        else:
            to_embed.append(i)
    del saved

    for start in range(0, len(to_embed), _EMBED_BATCH_SIZE):
        batch = to_embed[start:start + _EMBED_BATCH_SIZE]
        batch_vectors = np.asarray(embed_texts([texts[i] for i in batch]), dtype=np.float32)
        save_posting_embeddings(
            [(ids[i], hashes[i], batch_vectors[j]) for j, i in enumerate(batch)]
        )
        for j, i in enumerate(batch):
            vectors[i] = batch_vectors[j]
        del batch_vectors
        gc.collect()
        if progress:
            progress(min(start + _EMBED_BATCH_SIZE, len(to_embed)), len(to_embed))

    matrix = normalize_rows(np.vstack(vectors))
    metas = [
        {
            "id": p["id"],
            "company": p["company"],
            "title": p["title"],
            "location": p.get("location") or "",
            "url": p.get("url") or "",
            "salary_text": p.get("salary_text") or "",
        }
        for p in postings
    ]
    return PostingsIndex(matrix, metas)


def _match(meta: dict, similarity: Optional[float]) -> dict:
    return {
        "id": meta["id"],
        "company": meta["company"],
        "title": meta["title"],
        "location": meta["location"],
        "url": meta["url"],
        "salary_text": meta["salary_text"] or None,
        "similarity": similarity,
    }


def search_postings(
    index: PostingsIndex,
    role_query: str,
    location_filter: Optional[str] = None,
    top_k: int = 100,
) -> dict:
    """Semantic search on role_query against posting titles/skills;
    location_filter (if given) is applied as a case-insensitive
    substring/alias match on the posting's location, not semantic
    similarity — see module docstring for why.

    role_query is optional: if it's blank but location_filter is set,
    this does a location-only lookup (no semantic ranking, since
    there's no role text to embed).

    Returns {"matches": [...up to top_k...], "total": <full match count
    before truncation>} so callers can tell the difference between "no
    results" and "more results than top_k, only showing the first N" —
    silently capping without reporting the true total was hiding real
    postings from view.
    """
    if index.count() == 0:
        return {"matches": [], "total": 0}

    role_query = (role_query or "").strip()
    location_filter = (location_filter or "").strip() or None

    if not role_query:
        if not location_filter:
            return {"matches": [], "total": 0}
        # Location-only: no query to embed, so just filter by location.
        # No early break -- we need the TRUE total match count, not just
        # the first top_k, so results aren't silently cut.
        all_matches = [
            _match(meta, None)
            for meta in index.metas
            if location_matches(location_filter, meta["location"])
        ]
        return {"matches": all_matches[:top_k], "total": len(all_matches)}

    query_vector = normalize_rows(embed_texts([role_query]))[0]
    similarities = index.vectors @ query_vector  # cosine similarity per posting
    order = np.argsort(-similarities)  # best first
    if not location_filter:
        # Same candidate pool as before (top_k * 4). With a location
        # filter every posting is considered, since we can't know ahead
        # of time how many of the top semantic matches will also pass it.
        order = order[: max(top_k * 4, top_k)]

    all_matches = []
    for i in order:
        meta = index.metas[i]
        if location_filter and not location_matches(location_filter, meta["location"]):
            continue
        all_matches.append(_match(meta, round(float(similarities[i]), 3)))

    return {"matches": all_matches[:top_k], "total": len(all_matches)}