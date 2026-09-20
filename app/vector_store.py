"""
Phase 4 — Vector storage & similarity search.

One small in-memory index per matching "session" (i.e., per JD + batch
of resumes): a NumPy matrix of L2-normalised chunk embeddings plus
parallel lists of doc names / section names. Cosine similarity is then
just a matrix-vector product.

This replaces the earlier ephemeral ChromaDB collection. For a few
hundred chunks a brute-force dot product is exact (no approximate
index), takes well under a millisecond, and avoids Chroma's ~60 MB
import cost. It also avoids a leak: chromadb.EphemeralClient() shares
one in-process system, so every collection created per ranking run
stayed alive for the life of the server. Here, an index is garbage
collected as soon as the request that built it is done.

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

from typing import Dict, List

import numpy as np

from app.embeddings import embed_texts


def normalize_rows(vectors: np.ndarray) -> np.ndarray:
    """L2-normalise each row (float32) so that a dot product IS the
    cosine similarity -- the same thing Chroma's "cosine" space did."""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.size == 0:
        return vectors
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


class ChunkIndex:
    """In-memory index of resume chunks. Exposes count() so callers that
    used to call collection.count() keep working unchanged."""

    def __init__(self, vectors: np.ndarray, doc_names: List[str], sections: List[str]):
        self.vectors = vectors
        self.doc_names = doc_names
        self.sections = sections

    def count(self) -> int:
        return len(self.doc_names)


def build_collection(chunks: List[dict]) -> ChunkIndex:
    """Embed a list of resume chunk records and hold them in an
    in-memory ChunkIndex. Each chunk dict must have doc_name, section,
    text.
    """
    if not chunks:
        return ChunkIndex(np.zeros((0, 0), dtype=np.float32), [], [])

    vectors = normalize_rows(embed_texts([c["text"] for c in chunks]))
    return ChunkIndex(
        vectors,
        [c["doc_name"] for c in chunks],
        [c["section"] for c in chunks],
    )


def score_resumes_against_jd(collection: ChunkIndex, jd_chunks: List[dict]) -> Dict[str, dict]:
    """For each JD chunk, score every resume chunk against it and record
    the best (max) similarity seen per resume, per JD section —
    implementing the max-pooling decision described above.

    Returns: {
        doc_name: {
            "best_score": float,             # overall max across all JD chunks
            "section_matches": {jd_section: (resume_section, score)},
        }
    }
    """
    results: Dict[str, dict] = {}
    if collection.count() == 0 or not jd_chunks:
        return results

    jd_vectors = normalize_rows(embed_texts([c["text"] for c in jd_chunks]))

    for jd_chunk, jd_vector in zip(jd_chunks, jd_vectors):
        similarities = collection.vectors @ jd_vector  # cosine sim, one per resume chunk
        jd_section = jd_chunk["section"]

        for doc_name, section, sim in zip(collection.doc_names, collection.sections, similarities):
            similarity = float(sim)

            entry = results.setdefault(doc_name, {"best_score": -1.0, "section_matches": {}})
            if similarity > entry["best_score"]:
                entry["best_score"] = similarity

            current = entry["section_matches"].get(jd_section)
            if current is None or similarity > current[1]:
                entry["section_matches"][jd_section] = (section, similarity)

    return results