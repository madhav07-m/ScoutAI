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

Reuses the SAME embedding model and ephemeral-ChromaDB pattern already
built for resume/JD matching (app/embeddings.py, app/vector_store.py)
— no new model, no extra cost. The postings collection is rebuilt in
memory each time postings are refreshed (see streamlit_app.py), not
persisted, since re-embedding a few hundred short titles is fast and
keeps this consistent with the existing ephemeral-per-session design.
"""

import gc
import uuid
from typing import List, Optional

import chromadb

from app.embeddings import embed_texts
from app.location_aliases import location_matches


def build_postings_collection(postings: List[dict]):
    """Embed each posting's title (+ skills, for a bit more signal)
    and store in a fresh ephemeral ChromaDB collection. Each posting
    dict must have id, company, title, location, url, salary_text,
    skills (see companies_store.get_all_postings).
    """
    client = chromadb.EphemeralClient()
    collection = client.create_collection(f"postings_{uuid.uuid4().hex[:8]}")

    if not postings:
        return collection

    texts = [
        p["title"] + (" " + " ".join(p["skills"]) if p.get("skills") else "")
        for p in postings
    ]
    ids = [p["id"] for p in postings]
    metadatas = [
        {
            "company": p["company"],
            "title": p["title"],
            "location": p.get("location") or "",
            "url": p.get("url") or "",
            "salary_text": p.get("salary_text") or "",
        }
        for p in postings
    ]

    # Embedding all postings in one call was the actual memory spike
    # here (thousands of texts held in memory through the model at
    # once), not the Chroma insert below (which was already batched).
    # Embed in small batches instead and add each batch to the
    # collection as it's ready, so peak memory stays bounded regardless
    # of how many postings there are.
    _EMBED_BATCH_SIZE = 200
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        end = start + _EMBED_BATCH_SIZE
        batch_vectors = embed_texts(texts[start:end])
        collection.add(
            ids=ids[start:end],
            embeddings=batch_vectors.tolist(),
            documents=texts[start:end],
            metadatas=metadatas[start:end],
        )
        del batch_vectors
        gc.collect()
    return collection


def search_postings(
    collection,
    role_query: str,
    location_filter: Optional[str] = None,
    top_k: int = 100,
) -> dict:
    """Semantic search on role_query against posting titles/skills;
    location_filter (if given) is applied as a case-insensitive
    substring match on the posting's location, not semantic similarity
    — see module docstring for why.

    role_query is optional: if it's blank but location_filter is set,
    this does a location-only lookup (no semantic ranking, since
    there's no role text to embed).

    Returns {"matches": [...up to top_k...], "total": <full match count
    before truncation>} so callers can tell the difference between "no
    results" and "more results than top_k, only showing the first N" —
    silently capping without reporting the true total was hiding real
    postings from view.
    """
    count = collection.count()
    if count == 0:
        return {"matches": [], "total": 0}

    role_query = (role_query or "").strip()
    loc_filter_lower = location_filter.strip().lower() if location_filter else None

    if not role_query:
        if not loc_filter_lower:
            return {"matches": [], "total": 0}
        # Location-only: no query to embed, so just list everything
        # and filter by location instead of doing a vector search.
        # No early break here -- we need the TRUE total match count,
        # not just the first top_k, so results aren't silently cut.
        all_items = collection.get()
        all_matches = []
        for _id, meta in zip(all_items["ids"], all_items["metadatas"]):
            if not location_matches(location_filter, meta.get("location") or ""):
                continue
            all_matches.append({
                "id": _id,
                "company": meta["company"],
                "title": meta["title"],
                "location": meta["location"],
                "url": meta["url"],
                "salary_text": meta["salary_text"] or None,
                "similarity": None,
            })
        return {"matches": all_matches[:top_k], "total": len(all_matches)}

    query_vector = embed_texts([role_query])[0]
    # Query the WHOLE collection (not just top_k*4) when a location
    # filter is active, since we don't know ahead of time how many of
    # the top semantic matches will also pass the location filter --
    # under-fetching here is exactly what caused results to look
    # incomplete before.
    n_results = count if loc_filter_lower else min(count, max(top_k * 4, top_k))
    result = collection.query(
        query_embeddings=[query_vector.tolist()],
        n_results=n_results,
    )

    all_matches = []
    ids = result["ids"][0]
    distances = result["distances"][0]
    metadatas = result["metadatas"][0]

    for _id, dist, meta in zip(ids, distances, metadatas):
        if location_filter and not location_matches(location_filter, meta.get("location") or ""):
            continue
        all_matches.append({
            "id": _id,
            "company": meta["company"],
            "title": meta["title"],
            "location": meta["location"],
            "url": meta["url"],
            "salary_text": meta["salary_text"] or None,
            "similarity": round(1 - dist, 3),
        })

    return {"matches": all_matches[:top_k], "total": len(all_matches)}