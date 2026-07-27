"""
Phase 4 — Vector storage & similarity search.

Uses an in-memory/ephemeral ChromaDB client, one collection per
matching "session" (i.e., per JD + batch of resumes), since this is a
stateless Streamlit app rather than a persistent service.

Pooling decision: chunk-level MAX-POOLING, not averaging.

Why: a resume can be a strong fit even if only ONE section (say,
Experience) matches the JD extremely well, while other sections
(Education, Certifications) are irrelevant to it. Averaging chunk
scores would drag a genuinely strong match down because of unrelated
sections. Max-pooling — taking the single best chunk-to-JD-chunk
score per resume — better reflects "does this resume have real
evidence of the thing the JD is asking for," which is closer to how a
human reviewer skims a resume (they look for the best evidence, not
the average impression). The trade-off: max-pooling can be fooled by
one lucky section in an otherwise weak resume, which is exactly why
Phase 5's normalization step exists on top of it.
"""

import uuid
from typing import Dict, List

import chromadb
import numpy as np

from app.embeddings import embed_texts


def build_collection(chunks: List[dict]):
    """Embed a list of resume chunk records and store them in a fresh
    ephemeral ChromaDB collection. Each chunk dict must have
    doc_name, section, text.
    """
    client = chromadb.EphemeralClient()
    collection_name = f"resumes_{uuid.uuid4().hex[:8]}"
    # IMPORTANT: Chroma defaults to L2 (squared Euclidean) distance if
    # not told otherwise. score_resumes_against_jd() below assumes
    # cosine distance (similarity = 1 - dist), so without this explicit
    # metadata, "dist" is an L2 distance, similarity goes deeply
    # negative, and every Fit Score gets clamped to 0 in ranking.py.
    collection = client.create_collection(
        collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    texts = [c["text"] for c in chunks]
    if not texts:
        return collection

    vectors = embed_texts(texts)
    ids = [f"{c['doc_name']}::{c['section']}::{i}" for i, c in enumerate(chunks)]
    metadatas = [{"doc_name": c["doc_name"], "section": c["section"]} for c in chunks]

    collection.add(
        ids=ids,
        embeddings=vectors.tolist(),
        documents=texts,
        metadatas=metadatas,
    )
    return collection


def score_resumes_against_jd(collection, jd_chunks: List[dict]) -> Dict[str, dict]:
    """For each JD chunk, query the resume-chunk collection and record
    the best (max) similarity score seen per resume, per JD section —
    implementing the max-pooling decision described above.

    Returns: {
        doc_name: {
            "best_score": float,             # overall max across all JD chunks
            "section_matches": {jd_section: (resume_section, score)},
        }
    }
    """
    results: Dict[str, dict] = {}

    for jd_chunk in jd_chunks:
        jd_vector = embed_texts([jd_chunk["text"]])[0]
        query_result = collection.query(
            query_embeddings=[jd_vector.tolist()],
            n_results=max(collection.count(), 1),
        )

        ids = query_result["ids"][0]
        distances = query_result["distances"][0]  # cosine distance (1 - cos_sim) for normalized vectors
        metadatas = query_result["metadatas"][0]

        for _id, dist, meta in zip(ids, distances, metadatas):
            similarity = 1 - dist  # convert distance back to cosine similarity
            doc_name = meta["doc_name"]
            section = meta["section"]

            entry = results.setdefault(doc_name, {"best_score": -1.0, "section_matches": {}})
            if similarity > entry["best_score"]:
                entry["best_score"] = similarity

            jd_section = jd_chunk["section"]
            current = entry["section_matches"].get(jd_section)
            if current is None or similarity > current[1]:
                entry["section_matches"][jd_section] = (section, similarity)

    return results
