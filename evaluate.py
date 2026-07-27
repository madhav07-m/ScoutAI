"""
Phase 7 — Evaluation (the highest-value phase — don't skip this).

This script is meant to be run standalone (not through Streamlit):

    python evaluate.py --jd sample_data/jd.txt --labels sample_data/labels.csv

`labels.csv` format:
    resume_filename,label
    strong_match_1.pdf,strong
    medium_match_1.pdf,medium
    weak_match_1.pdf,weak

What it does:
1. Runs the full pipeline (parse -> chunk -> embed -> store -> score -> rank)
   on the same resumes you hand-labeled.
2. Maps labels to an expected order (strong > medium > weak).
3. Computes Spearman rank correlation between the system's ranking and
   your hand-labeled expected ranking — a simple, defensible way to
   turn "it works" into "here's how well it works, numerically."

You need 10-15 hand-labeled resume/JD pairs for this to mean anything.
Labeling them yourself (or with a friend) is the point — this is
supposed to encode YOUR judgment as the ground truth to check the
system against.
"""

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

sys.path.append(str(Path(__file__).parent))

from app.parsing import parse_document
from app.chunking import chunk_by_section, chunks_to_list
from app.vector_store import build_collection, score_resumes_against_jd
from app.ranking import normalize_and_rank

LABEL_ORDER = {"strong": 3, "medium": 2, "weak": 1}


def load_labels(labels_csv: str) -> dict:
    labels = {}
    with open(labels_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            labels[row["resume_filename"]] = row["label"].strip().lower()
    return labels


def run_pipeline(jd_path: str, resume_dir: str, resume_filenames: list) -> list:
    jd_bytes = Path(jd_path).read_bytes()
    jd_text = parse_document(Path(jd_path).name, jd_bytes)
    jd_sections = chunk_by_section(jd_text)
    jd_chunks = chunks_to_list("JD", jd_sections)

    all_chunks = []
    chunk_counts = {}
    for fname in resume_filenames:
        path = Path(resume_dir) / fname
        text = parse_document(fname, path.read_bytes())
        sections = chunk_by_section(text)
        chunks = chunks_to_list(fname, sections)
        all_chunks.extend(chunks)
        chunk_counts[fname] = len(chunks)

    collection = build_collection(all_chunks)
    scores = score_resumes_against_jd(collection, jd_chunks)
    ranked = normalize_and_rank(scores, chunk_counts)
    return ranked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jd", required=True, help="Path to JD text/pdf/docx file")
    parser.add_argument("--resume_dir", required=True, help="Directory containing labeled resumes")
    parser.add_argument("--labels", required=True, help="CSV with resume_filename,label")
    args = parser.parse_args()

    labels = load_labels(args.labels)
    resume_filenames = list(labels.keys())

    ranked = run_pipeline(args.jd, args.resume_dir, resume_filenames)

    system_order = [r["doc_name"] for r in ranked]
    expected_scores = [LABEL_ORDER[labels[name]] for name in system_order]
    system_scores = [r["fit_score"] for r in ranked]

    corr, p_value = spearmanr(system_scores, expected_scores)

    print("\n=== Ranking produced by the system ===")
    for r in ranked:
        print(f"{r['doc_name']:30s} fit={r['fit_score']:6.1f}  label={labels[r['doc_name']]}")

    print(f"\nSpearman rank correlation vs. hand-labeled ground truth: {corr:.3f} (p={p_value:.3f})")
    print("(1.0 = perfect agreement with your labels, 0 = no correlation, negative = inverted)")


if __name__ == "__main__":
    main()
