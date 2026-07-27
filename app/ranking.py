"""
Phase 5 — Ranking logic.

fit_score = max-pooled cosine similarity x 100, shown directly.

No batch-relative rescaling and no length penalty — the number you
see is the resume's actual similarity to the JD, comparable across
runs. Trade-off worth stating out loud: MiniLM cosine similarities for
real text tend to cluster in a fairly narrow band (often ~40-75), so
scores across different resumes may look closer together than a
rescaled version would, and a resume with more chunks still gets more
chances at a high max-pooled score (a mild length bias). Both are
documented limitations rather than hidden defaults.

This embedding-based fit_score is intentionally kept separate from the
LLM-assessed score generated later (Phase 6) — one is a similarity
calculation, the other is Gemini's qualitative judgment, and the two
can legitimately disagree (e.g. the LLM can reason about eligibility
criteria like graduation year that vector similarity can't capture).
"""

from typing import Dict, List

# Thresholds for the Strong/Average/Weak label shown alongside Fit
# Score. These are deliberately conservative given the documented
# clustering behavior above (MiniLM cosine scores for real resume/JD
# text tend to sit in a ~40-75 band) — treat them as a reasonable
# starting point, not a scientifically calibrated cutoff. Adjust here
# if your own hand-labeled eval data (see evaluate.py) suggests
# different boundaries fit your resume set better.
STRONG_MATCH_THRESHOLD = 65
WEAK_MATCH_THRESHOLD = 45


def classify_match(fit_score: float) -> str:
    """Bucket a 0-100 fit_score into Strong / Average / Weak.
    Pure threshold logic, no ML — same fit_score number the table
    already shows, just labeled for at-a-glance scanning of a long
    resume list.
    """
    if fit_score >= STRONG_MATCH_THRESHOLD:
        return "Strong"
    elif fit_score < WEAK_MATCH_THRESHOLD:
        return "Weak"
    return "Average"


def normalize_and_rank(
    scores: Dict[str, dict],
    chunk_counts: Dict[str, int],
) -> List[dict]:
    """Take raw per-resume best_score (max-pooled cosine similarity)
    and convert directly to a 0-100 fit score, sorted descending.
    chunk_counts is accepted for interface compatibility but not used
    to penalize length — see module docstring.
    """
    if not scores:
        return []

    ranked = []
    for doc_name, entry in scores.items():
        raw = entry["best_score"]
        fit_score = round(max(raw, 0) * 100, 1)
        ranked.append({
            "doc_name": doc_name,
            "fit_score": fit_score,
            "match_category": classify_match(fit_score),
            "section_matches": entry["section_matches"],
        })

    ranked.sort(key=lambda r: r["fit_score"], reverse=True)
    return ranked
