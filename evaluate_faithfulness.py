"""
Phase 7b — RAG faithfulness evaluation (the part that was missing).

evaluate.py (Phase 7) checks RANKING quality — does resume order match
your hand-labeled strong/medium/weak judgments. It says nothing about
whether the GAP ANALYSIS REPORTS (Phase 6) are actually grounded in
the retrieved chunks, or just plausible-sounding LLM output.

This script closes that gap using RAGAS, with Gemini as the judge
model (free — reuses the same API key already used for gap analysis,
no separate paid judge needed).

Two metrics computed per report:
- faithfulness: of the claims made in the generated report (strengths
  + gaps + suggestions), what fraction are actually supported by the
  retrieved resume context? This is the automated version of the
  "spot-check reports against source resume manually" step in the
  original plan.
- answer_relevancy: does the generated report actually address the
  JD/resume match question, rather than drifting into generic advice?

Usage (API key from .env, same as the Streamlit app):
    python evaluate_faithfulness.py \
        --jd sample_data/jd_backend_engineer.txt --resume_dir sample_data/resumes \
        --resumes strong_match_1.txt medium_match_1.txt

Or pass the key explicitly instead of using .env:
    python evaluate_faithfulness.py --api_key YOUR_GEMINI_KEY \
        --jd sample_data/jd_backend_engineer.txt --resume_dir sample_data/resumes \
        --resumes strong_match_1.txt medium_match_1.txt

This runs the real pipeline (parse -> chunk -> embed -> retrieve ->
generate gap analysis) for each named resume, then scores the
generated report against the retrieved context with RAGAS.
"""

import argparse
import os
import sys
import time
import threading
from pathlib import Path

from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent))

from datasets import Dataset
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy
from ragas.run_config import RunConfig

from app.parsing import parse_document
from app.chunking import chunk_by_section, chunks_to_list
from app.vector_store import build_collection, score_resumes_against_jd
from app.gap_analysis import configure_gemini, generate_gap_analysis

load_dotenv()  # reads .env in this directory, same as streamlit_app.py


# ---------------------------------------------------------------------------
# Runtime patch for a confirmed bug in langchain-google-genai==1.0.10:
# ChatGoogleGenerativeAI._generate builds kwargs including a top-level
# "temperature" and forwards them straight to the raw, low-level
# GenerativeServiceClient.generate_content() (bypassing the friendly
# google.generativeai.GenerativeModel wrapper), which doesn't accept
# "temperature" as a direct kwarg -- it must be nested inside a
# GenerationConfig object instead. This has nothing to do with which
# google-generativeai version is installed; it reproduces on every
# combination tried (0.7.2 and 0.8.6). Rather than editing the
# installed library's source (fragile, gets wiped on every reinstall),
# we replace the two methods RAGAS actually calls (_generate /
# _agenerate) with a version that talks to google.generativeai
# directly using the correct call shape. This only affects THIS
# script's use of ChatGoogleGenerativeAI as the RAGAS judge model --
# nothing in app/gap_analysis.py or the main app is touched.
# ---------------------------------------------------------------------------
import google.generativeai as _genai
from google.generativeai.types import GenerationConfig as _GenerationConfig
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.messages import AIMessage


# ---------------------------------------------------------------------------
# Gemini free tier allows only 5 requests/minute for gemini-3.5-flash.
# Ragas fires metric calls concurrently (default max_workers), which blows
# past that limit even with tenacity retries -- the retries themselves
# eventually exhaust and the whole evaluate() call dies with
# ResourceExhausted. This lock + interval enforces a global minimum gap
# between calls across all threads Ragas spawns, so we never exceed the
# quota regardless of how many workers are running.
# ---------------------------------------------------------------------------
_RATE_LIMIT_LOCK = threading.Lock()
_LAST_CALL_TIME = [0.0]
_MIN_INTERVAL_SECONDS = 13.0  # ~4.6 req/min, safely under the 5 req/min cap


def _throttle():
    with _RATE_LIMIT_LOCK:
        now = time.monotonic()
        wait = _MIN_INTERVAL_SECONDS - (now - _LAST_CALL_TIME[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL_TIME[0] = time.monotonic()


def _patched_generate(self, messages, stop=None, run_manager=None, **kwargs):
    _throttle()
    prompt_text = "\n\n".join(
        m.content if hasattr(m, "content") else str(m) for m in messages
    )
    model = _genai.GenerativeModel(self.model.replace("models/", ""))
    response = model.generate_content(
        prompt_text,
        generation_config=_GenerationConfig(temperature=self.temperature or 0),
        request_options={"timeout": 60},
    )
    message = AIMessage(content=response.text)
    generation = ChatGeneration(message=message)
    return ChatResult(generations=[generation])


async def _patched_agenerate(self, messages, stop=None, run_manager=None, **kwargs):
    # RAGAS calls the async path -- reuse the sync implementation via a
    # thread so we don't need a separate async google.generativeai call.
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(
        None, lambda: _patched_generate(self, messages, stop, run_manager, **kwargs)
    )


ChatGoogleGenerativeAI._generate = _patched_generate
ChatGoogleGenerativeAI._agenerate = _patched_agenerate


def build_ragas_records(jd_path: str, resume_dir: str, resume_filenames: list, api_key: str) -> list:
    """Run the real pipeline for each resume and produce RAGAS-shaped
    records: question, contexts (retrieved chunks), answer (generated
    report flattened to text).
    """
    configure_gemini(api_key)

    jd_bytes = Path(jd_path).read_bytes()
    jd_text = parse_document(Path(jd_path).name, jd_bytes)
    jd_sections = chunk_by_section(jd_text)
    jd_chunks = chunks_to_list("JD", jd_sections)

    records = []
    for fname in resume_filenames:
        path = Path(resume_dir) / fname
        resume_text = parse_document(fname, path.read_bytes())
        resume_sections = chunk_by_section(resume_text)
        resume_chunks = chunks_to_list(fname, resume_sections)

        collection = build_collection(resume_chunks)
        scores = score_resumes_against_jd(collection, jd_chunks)
        entry = scores.get(fname)
        if entry is None:
            continue

        # The retrieved context RAGAS checks faithfulness against —
        # exactly the same matched sections passed into Gemini in
        # gap_analysis.py, kept consistent with Phase 6's grounding.
        matched_sections = {
            resume_section: resume_sections.get(resume_section, "")
            for _, (resume_section, _) in entry["section_matches"].items()
        }
        contexts = [f"[{name}] {text}" for name, text in matched_sections.items() if text]

        report = generate_gap_analysis(jd_sections, matched_sections)

        answer_text = (
            f"Fit score: {report.get('fit_score')}. "
            f"Strengths: {'; '.join(report.get('strengths', []))}. "
            f"Gaps: {'; '.join(report.get('gaps', []))}. "
            f"Suggestions: {'; '.join(report.get('suggestions', []))}."
        )

        records.append({
            "question": "How well does this resume fit this job description, and what are the gaps?",
            "contexts": contexts,
            "answer": answer_text,
            "resume_filename": fname,
        })

    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--api_key",
        required=False,
        default=None,
        help="Gemini API key. Optional -- if omitted, falls back to the "
             "GEMINI_API_KEY value from .env (same as the main Streamlit app).",
    )
    parser.add_argument("--jd", required=True, help="Path to JD file")
    parser.add_argument("--resume_dir", required=True, help="Directory containing resumes")
    parser.add_argument("--resumes", nargs="+", required=True, help="Resume filenames to evaluate")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            "No API key provided. Either pass --api_key YOUR_KEY, or set "
            "GEMINI_API_KEY=... in a .env file in this directory (same as "
            "the main Streamlit app uses)."
        )
        return

    records = build_ragas_records(args.jd, args.resume_dir, args.resumes, api_key)
    if not records:
        print("No records produced — check that resume filenames matched the JD chunks.")
        return

    dataset = Dataset.from_list([
        {"question": r["question"], "contexts": r["contexts"], "answer": r["answer"]}
        for r in records
    ])

    judge_llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", google_api_key=api_key, temperature=0)
    judge_embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001", google_api_key=api_key)

    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy],
        llm=judge_llm,
        embeddings=judge_embeddings,
        raise_exceptions=True,  # temporary diagnostic: surface the real traceback instead of the swallowed "Exception raised in Job[N]" summary
        run_config=RunConfig(max_workers=1, timeout=1800, max_retries=2),  # serialize calls: free-tier
                                                            # Gemini allows only 5 req/min; combined
                                                            # with the _throttle() in the patched
                                                            # _generate above, this keeps us under
                                                            # quota. timeout is generous (30 min)
                                                            # because faithfulness makes multiple
                                                            # sequential sub-calls per sample
                                                            # (statement extraction + per-statement
                                                            # verdicts), each throttled ~13s apart --
                                                            # the previous 180s default was getting
                                                            # hit mid-retry and cancelling the call.
    )

    df = result.to_pandas()
    df["resume_filename"] = [r["resume_filename"] for r in records]

    print("\n=== Faithfulness & relevance of generated gap-analysis reports ===")
    print(df[["resume_filename", "faithfulness", "answer_relevancy"]].to_string(index=False))
    print(f"\nMean faithfulness: {df['faithfulness'].mean():.3f}")
    print(f"Mean answer relevancy: {df['answer_relevancy'].mean():.3f}")
    print("\n(faithfulness = fraction of claims in the report traceable to the retrieved")
    print(" resume context; 1.0 = every claim grounded, lower = hallucinated content)")


if __name__ == "__main__":
    main()