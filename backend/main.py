"""
FastAPI backend for Scout AI.

This replaces streamlit_app.py as the way the existing app/ modules
(parsing, chunking, vector_store, ranking, gap_analysis, pdf_report,
companies_store, postings_search) are exposed to a user — instead of a
Streamlit UI, they're exposed as a small JSON API that the static
frontend (frontend/index.html, frontend/companies.html) calls with
fetch(). Nothing in app/ was changed; this is purely a new access
layer plus the two frontend files it serves.

Run with:
    uvicorn backend.main:app --reload --port 8000
(run from the resume-matcher/ project root, so `app` and `backend`
are both importable, and so relative paths like companies.db land in
the project root like they did for streamlit_app.py.)

Then open http://localhost:8000/ in a browser.
"""

import gc
import hashlib
import os
import threading
import uuid
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.chunking import chunk_by_section, chunks_to_list
from app.companies_store import (
    DB_PATH,
    DEFAULT_COMPANIES,
    get_all_postings,
    get_companies_overview,
    get_postings_for_company,
    refresh_all,
    refresh_company,
)
from app.gap_analysis import configure_gemini, generate_gap_analysis
from app.parsing import is_low_extraction, parse_document_with_meta
from app.pdf_report import build_gap_analysis_pdf
from app.postings_search import build_postings_collection, search_postings
from app.ranking import (
    STRONG_MATCH_THRESHOLD,
    WEAK_MATCH_THRESHOLD,
    classify_match,
    normalize_and_rank,
)
from app.session_store import load_all_sessions, load_session, prune_expired, save_session
from app.vector_store import build_collection, score_resumes_against_jd
from app.embeddings import embed_texts

app = FastAPI(title="Scout AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory session store.
#
# The Streamlit version kept jd_sections / resume_sections / gap report
# caches in st.session_state, scoped to one browser tab's session. A
# stateless FastAPI process has no equivalent for free, so a ranking
# run gets a session_id the frontend holds onto (e.g. in a JS variable)
# and passes back in for "regenerate gap analysis for this resume" /
# "download this PDF" follow-up calls, without re-uploading and
# re-parsing every file each time. This is intentionally in-process
# memory (fine for a single local user, same trust model as the
# original single-user Streamlit app) -- it is lost on server restart
# and not meant to be a multi-user production session store.
# ---------------------------------------------------------------------------
SESSIONS: Dict[str, dict] = {}
# Same idea for the companies postings search index -- rebuilt whenever
# companies are refreshed, kept in memory between requests.
_postings_collection = {"collection": None}
# Tracks the state of a companies refresh so the frontend can poll it.
# refresh_all() over 32 companies (some with retry+backoff on Workday)
# routinely takes well over Render's request timeout, so /api/companies/refresh
# can no longer do the fetch inline and return the result directly -- it
# kicks the fetch off as a background task and returns immediately, and
# the frontend polls /api/companies/refresh-status for progress instead.
_refresh_status = {"state": "idle", "failures": {}}  # state: idle | running | done | error


@app.on_event("startup")
async def _restore_sessions_on_startup():
    """Repopulate SESSIONS from sessions.db so ranking results made
    before a backend restart (or before the frontend simply navigated
    away and back) are still there — this is what fixes the
    'Session not found' error that a purely in-memory SESSIONS dict
    caused whenever the backend process restarted.
    """
    SESSIONS.update(load_all_sessions())
    prune_expired()


@app.on_event("startup")
async def _warm_embedding_model():
    """Force the embedding model's one-time download/load to happen
    now, during deploy startup, instead of lazily on whatever request
    happens to trigger it first.

    Without this, the FIRST real request that calls embed_texts()
    (ranking a resume, or a companies search/refresh that indexes
    postings) pays a ~60-100s one-time cost to download+load the
    fastembed ONNX model files. On Render specifically, that request
    then exceeds the platform's request timeout and comes back as a
    502, even though the backend itself hadn't crashed -- it was just
    still mid-download when Render gave up waiting. Startup has no
    such tight timeout, so paying this cost here instead is free.
    """
    try:
        embed_texts(["warmup"])
    except Exception as e:  # noqa: BLE001 - don't block startup if this fails; the
        # first real request will just pay the lazy-load cost as before
        print(f"Embedding model warmup failed (non-fatal): {e}")


@app.on_event("startup")
async def _auto_refresh_companies_on_startup():
    """Kick off a companies refresh + search-index build automatically
    when the backend starts, instead of waiting for someone to click
    "Refresh postings" by hand.

    This runs in a plain background thread (not asyncio.create_task)
    because refresh_company()/build_postings_collection() are
    synchronous, CPU/network-bound calls -- running them directly in
    the startup event would block the whole app from accepting any
    requests (including the port-binding health check) until they
    finished, which on Render's free-tier CPU can take several
    minutes. A daemon thread lets startup complete immediately while
    this keeps working in the background; _refresh_status and
    _index_status (polled by the frontend) reflect its progress.
    """
    def _startup_job():
        _run_refresh_in_background()  # fetches postings company-by-company, in batches
        _rebuild_postings_index()     # then builds the search index once, also batched
    threading.Thread(target=_startup_job, daemon=True).start()


def _content_cache_key(jd_sections: dict, matched_sections: dict) -> str:
    combined = "".join(f"{k}:{v}" for k, v in sorted(jd_sections.items()))
    combined += "||" + "".join(f"{k}:{v}" for k, v in sorted(matched_sections.items()))
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _matched_sections_for(session: dict, doc_name: str, r: dict) -> Dict[str, str]:
    resume_sections = session["resume_sections"][doc_name]
    return {
        jd_section: resume_sections.get(resume_section, "")
        for jd_section, (resume_section, _sim) in r["section_matches"].items()
    }


def _run_gap_analysis(session: dict, r: dict) -> dict:
    matched_sections = _matched_sections_for(session, r["doc_name"], r)
    key = _content_cache_key(session["jd_sections"], matched_sections)
    cache = session.setdefault("gap_reports_by_hash", {})
    if key not in cache:
        cache[key] = generate_gap_analysis(session["jd_sections"], matched_sections)
    return cache[key]


def _serialize_ranked(session: dict) -> List[dict]:
    ranked = session["ranked"]
    gap_reports = session.get("gap_reports", {})
    out = []
    for r in ranked:
        report = gap_reports.get(r["doc_name"])
        llm_score = report.get("fit_score") if report else None
        category = classify_match(llm_score) if llm_score is not None else r["match_category"]
        out.append({
            "doc_name": r["doc_name"],
            "fit_score": r["fit_score"],
            "llm_score": llm_score,
            "match_category": category,
            "section_matches": {
                jd_section: {"resume_section": resume_section, "similarity": round(sim, 3)}
                for jd_section, (resume_section, sim) in r["section_matches"].items()
            },
            "gap_report": report,
        })
    return out


# ---------------------------------------------------------------------------
# Ranking endpoints
# ---------------------------------------------------------------------------

@app.post("/api/rank")
async def rank_resumes(
    jd: UploadFile = File(...),
    resumes: List[UploadFile] = File(...),
    gemini_key: Optional[str] = Form(None),
):
    gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY")

    jd_bytes = await jd.read()
    jd_text = parse_document_with_meta(jd.filename, jd_bytes)["text"]
    jd_sections = chunk_by_section(jd_text)
    jd_chunks = chunks_to_list("JD", jd_sections)

    all_chunks = []
    chunk_counts = {}
    resume_section_cache = {}
    low_extraction_docs = []
    ocr_used_docs = []

    for rf in resumes:
        content = await rf.read()
        parsed = parse_document_with_meta(rf.filename, content)
        text = parsed["text"]
        if parsed["method"] == "ocr":
            ocr_used_docs.append(rf.filename)
        if is_low_extraction(text):
            low_extraction_docs.append(rf.filename)
        sections = chunk_by_section(text)
        resume_section_cache[rf.filename] = sections
        chunks = chunks_to_list(rf.filename, sections)
        all_chunks.extend(chunks)
        chunk_counts[rf.filename] = len(chunks)

    collection = build_collection(all_chunks)
    scores = score_resumes_against_jd(collection, jd_chunks)
    ranked = normalize_and_rank(scores, chunk_counts)

    if not ranked:
        raise HTTPException(400, "Could not extract any usable text from the uploaded files.")

    session_id = uuid.uuid4().hex
    session = {
        "jd_sections": jd_sections,
        "resume_sections": resume_section_cache,
        "ranked": ranked,
        "gap_reports": {},
        "gap_reports_by_hash": {},
        "gemini_key": gemini_key,
        "low_extraction_docs": low_extraction_docs,
        "ocr_used_docs": ocr_used_docs,
    }
    SESSIONS[session_id] = session
    save_session(session_id, session)

    gemini_error = None
    if gemini_key:
        try:
            configure_gemini(gemini_key)
            quota_exhausted = False
            for r in ranked:
                if quota_exhausted:
                    session["gap_reports"][r["doc_name"]] = {
                        "fit_score": None,
                        "strengths": [],
                        "gaps": ["Skipped: Gemini daily quota already exhausted earlier in this batch. Try again after the quota resets, or switch to a model with a higher free-tier limit."],
                        "suggestions": [],
                    }
                    continue
                try:
                    session["gap_reports"][r["doc_name"]] = _run_gap_analysis(session, r)
                except Exception as e:  # noqa: BLE001 - surfaced per-resume, like the Streamlit version
                    if "429" in str(e) or "quota" in str(e).lower():
                        quota_exhausted = True
                    session["gap_reports"][r["doc_name"]] = {
                        "fit_score": None,
                        "strengths": [],
                        "gaps": [f"Gap analysis failed: {e}"],
                        "suggestions": [],
                    }
        except Exception as e:  # noqa: BLE001
            gemini_error = str(e)
        save_session(session_id, session)

    counts = {"Strong": 0, "Average": 0, "Weak": 0}
    for row in _serialize_ranked(session):
        counts[row["match_category"]] += 1

    return {
        "session_id": session_id,
        "ranked": _serialize_ranked(session),
        "counts": counts,
        "thresholds": {"strong": STRONG_MATCH_THRESHOLD, "weak": WEAK_MATCH_THRESHOLD},
        "low_extraction_docs": low_extraction_docs,
        "ocr_used_docs": ocr_used_docs,
        "has_gemini_key": bool(gemini_key),
        "gemini_error": gemini_error,
    }


def _get_session(session_id: str) -> dict:
    session = SESSIONS.get(session_id)
    if session is None:
        session = load_session(session_id)
        if session is not None:
            SESSIONS[session_id] = session  # warm the in-memory cache
    if session is None:
        raise HTTPException(404, "Session not found or expired — re-run ranking.")
    return session


@app.post("/api/rank/{session_id}/gap-analysis/{doc_name}")
async def regenerate_gap_analysis(session_id: str, doc_name: str, gemini_key: Optional[str] = Form(None)):
    session = _get_session(session_id)
    r = next((row for row in session["ranked"] if row["doc_name"] == doc_name), None)
    if r is None:
        raise HTTPException(404, f"No such resume in this session: {doc_name}")

    key = gemini_key or session.get("gemini_key")
    if not key:
        raise HTTPException(400, "No Gemini API key provided (pass one or set GEMINI_API_KEY).")

    configure_gemini(key)
    matched_sections = _matched_sections_for(session, doc_name, r)
    try:
        report = generate_gap_analysis(session["jd_sections"], matched_sections)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Gap analysis failed: {e}")

    session["gap_reports"][doc_name] = report
    cache_key = _content_cache_key(session["jd_sections"], matched_sections)
    session.setdefault("gap_reports_by_hash", {})[cache_key] = report
    save_session(session_id, session)
    return {"doc_name": doc_name, "gap_report": report}


@app.get("/api/rank/{session_id}/gap-analysis/{doc_name}/pdf")
async def download_gap_analysis_pdf(session_id: str, doc_name: str):
    session = _get_session(session_id)
    r = next((row for row in session["ranked"] if row["doc_name"] == doc_name), None)
    if r is None:
        raise HTTPException(404, f"No such resume in this session: {doc_name}")
    report = session["gap_reports"].get(doc_name)
    if not report:
        raise HTTPException(400, "No gap analysis generated yet for this resume.")

    pdf_bytes = build_gap_analysis_pdf(resume_name=doc_name, fit_score=r["fit_score"], llm_report=report)
    safe_name = doc_name.rsplit(".", 1)[0]
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="gap_analysis_{safe_name}.pdf"'},
    )


# ---------------------------------------------------------------------------
# Companies endpoints
# ---------------------------------------------------------------------------

_index_status = {"state": "idle", "last_error": None}  # idle | building | ready


_MAX_INDEXED_POSTINGS = 600  # search feature's scope, capped for two reasons:
# (1) keeps the embedding dataset small enough to build within Render's
# free-tier 512MB memory limit, and (2) directly cuts how long building the
# index takes -- embedding time scales with postings count, so indexing all
# 5588 real postings was both a memory risk AND took several minutes on
# Render's free-tier CPU. This trims what gets searched, not what's fetched/
# stored (get_companies_overview / get_postings_for_company still show
# everything; only the semantic search index is capped).


def _rebuild_postings_index():
    try:
        all_postings = get_all_postings(db_path=DB_PATH)
        capped = all_postings[:_MAX_INDEXED_POSTINGS]
        _postings_collection["collection"] = build_postings_collection(capped)
        _index_status["state"] = "ready"
        _index_status["last_error"] = None
    except Exception as e:  # noqa: BLE001 - surface via the search response instead of failing silently
        _index_status["state"] = "idle"
        _index_status["last_error"] = str(e)


@app.get("/api/companies/overview")
async def companies_overview():
    return {"companies": get_companies_overview(db_path=DB_PATH)}


_REFRESH_BATCH_SIZE = 5  # process companies in small chunks, not all 32 at once,
                          # so peak memory stays lower and gc has a chance to run
                          # between batches instead of everything staying live at once


def _run_refresh_in_background():
    _refresh_status["state"] = "running"
    failures = {}
    try:
        companies = DEFAULT_COMPANIES
        for i in range(0, len(companies), _REFRESH_BATCH_SIZE):
            batch = companies[i:i + _REFRESH_BATCH_SIZE]
            for cfg in batch:
                try:
                    refresh_company(cfg, db_path=DB_PATH)
                except Exception as e:  # noqa: BLE001 - one company failing shouldn't stop the rest
                    failures[cfg["name"]] = str(e)
            # Drop the batch's response data before starting the next one,
            # and explicitly free memory rather than waiting for it to
            # accumulate across all 32 companies before Python's own GC
            # would naturally run.
            gc.collect()

        # Don't rebuild the (memory-heavy, embeds every posting) search
        # index here -- that was the second half of what pushed memory
        # over the limit right after a refresh. Instead, just mark the
        # in-memory index stale; /api/companies/search rebuilds it lazily
        # on the next search request, spreading that cost out instead of
        # stacking it directly on top of the refresh.
        _postings_collection["collection"] = None
        _index_status["state"] = "idle"

        _refresh_status["failures"] = failures
        _refresh_status["state"] = "done"
    except Exception as e:  # noqa: BLE001 - surface to the status endpoint rather than crashing silently
        _refresh_status["state"] = "error"
        _refresh_status["failures"] = {"_all": str(e)}


@app.post("/api/companies/refresh")
async def companies_refresh(background_tasks: BackgroundTasks):
    if _refresh_status["state"] == "running":
        return {"status": "already_running"}
    _refresh_status["state"] = "running"
    _refresh_status["failures"] = {}
    background_tasks.add_task(_run_refresh_in_background)
    return {"status": "started"}


@app.get("/api/companies/refresh-status")
async def companies_refresh_status():
    return {
        "state": _refresh_status["state"],
        "failures": _refresh_status["failures"],
        "companies": get_companies_overview(db_path=DB_PATH),
    }


@app.get("/api/companies/{company}/postings")
async def companies_postings(company: str):
    return {"company": company, "postings": get_postings_for_company(company, db_path=DB_PATH)}


@app.get("/api/companies/search")
async def companies_search(background_tasks: BackgroundTasks, role: str = "", location: str = ""):
    if _postings_collection["collection"] is None:
        if _index_status["state"] == "idle":
            _index_status["state"] = "building"  # set immediately, before the task actually
            # runs, so a second request arriving milliseconds later (e.g. from typing
            # letter by letter) doesn't also see "idle" and schedule a duplicate build
            background_tasks.add_task(_rebuild_postings_index)
        return {"matches": [], "total": 0, "indexed": False, "building": True, "last_error": _index_status["last_error"]}
    collection = _postings_collection["collection"]
    if collection is None or collection.count() == 0:
        return {"matches": [], "total": 0, "indexed": False, "building": False}
    result = search_postings(collection, role, location, top_k=100)
    result["indexed"] = True
    result["building"] = False
    return result


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))


@app.get("/index.html")
async def serve_index_html():
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))


@app.get("/companies.html")
async def serve_companies():
    return FileResponse(os.path.join(_FRONTEND_DIR, "companies.html"))