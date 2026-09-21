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
import random
import threading
import uuid
from collections import OrderedDict
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.chunking import chunk_by_section, chunks_to_list
from app.auth import CurrentUser, get_current_user
from app.companies_store import (
    DB_PATH,
    DEFAULT_COMPANIES,
    get_all_postings,
    get_companies_overview,
    get_postings_for_company,
    prune_posting_embeddings,
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
from app.session_store import (
    MAX_SESSIONS_IN_MEMORY,
    load_all_sessions,
    load_session,
    prune_expired,
    save_session,
)
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
class LRUSessionCache(OrderedDict):
    """SESSIONS used to be a plain dict -- every new /api/rank call
    (line ~244 below) and every disk-fallback warm (in _get_session)
    added an entry that was NEVER evicted for the life of the process.
    session_store.py's load_all_sessions() cap (MAX_SESSIONS_IN_MEMORY)
    only bounds what gets preloaded at STARTUP; it does nothing about
    growth during a long-running process, which is what actually drove
    /api/health's sessions_in_memory climbing with no ceiling and
    pushed Render's free 512MB instance toward OOM under real use.

    This caps SESSIONS itself at MAX_SESSIONS_IN_MEMORY, evicting the
    least-recently-used entry once that's exceeded (both inserting and
    reading a session count as "used", so an actively-revisited session
    -- e.g. someone regenerating gap analysis a few times -- won't get
    evicted out from under them while they're using it). Eviction only
    drops a session from RAM, not from Postgres: _get_session() already
    falls back to load_session(id) on a cache miss, so an evicted
    session transparently reloads from disk (one DB round-trip) the
    next time it's actually needed, instead of the old 'Session not
    found' 404.
    """

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > MAX_SESSIONS_IN_MEMORY:
            self.popitem(last=False)  # evict least-recently-used

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
        return super().get(key, default)


SESSIONS: LRUSessionCache = LRUSessionCache()
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


def _persistable(session: dict) -> dict:
    """Copy of a session that is safe to write to the database: without
    the user's Gemini API key. The key stays in the in-memory SESSIONS
    entry only (so "regenerate gap analysis" keeps working during that
    session) and is never stored in Postgres. After a restart it's gone,
    and the frontend simply sends the key again when the person has one
    typed in."""
    return {k: v for k, v in session.items() if k != "gemini_key"}


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
    user: CurrentUser = Depends(get_current_user),
):
    user_gemini_key = gemini_key  # what the person typed, if anything
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
        "gemini_key": user_gemini_key,  # in memory only -- see _persistable()
        "low_extraction_docs": low_extraction_docs,
        "ocr_used_docs": ocr_used_docs,
    }
    SESSIONS[session_id] = session
    save_session(session_id, _persistable(session))

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
        save_session(session_id, _persistable(session))

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
async def regenerate_gap_analysis(session_id: str, doc_name: str, gemini_key: Optional[str] = None):
    # gemini_key is a plain query param (not Form/multipart) on purpose:
    # when no key is typed in the UI, the frontend sends this request
    # with no body at all so it can fall back to session/env, and an
    # empty multipart body (Content-Type: multipart/form-data with a
    # 0-byte payload) makes python-multipart raise "There was an error
    # parsing the body" -- a query param sidesteps that entirely since
    # there's no body to parse either way.
    session = _get_session(session_id)
    r = next((row for row in session["ranked"] if row["doc_name"] == doc_name), None)
    if r is None:
        raise HTTPException(404, f"No such resume in this session: {doc_name}")

    key = gemini_key or session.get("gemini_key") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise HTTPException(400, "No Gemini API key provided (pass one, or set GEMINI_API_KEY on the server).")

    configure_gemini(key)
    matched_sections = _matched_sections_for(session, doc_name, r)
    try:
        report = generate_gap_analysis(session["jd_sections"], matched_sections)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Gap analysis failed: {e}")

    session["gap_reports"][doc_name] = report
    cache_key = _content_cache_key(session["jd_sections"], matched_sections)
    session.setdefault("gap_reports_by_hash", {})[cache_key] = report
    save_session(session_id, _persistable(session))
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

# Cap how many postings get indexed, to cut memory -- the NumPy matrix
# itself is small either way (~8MB for 5,500 postings per
# app/postings_search.py's own numbers), but building the index still
# means holding `all_postings` (a Python list of dicts) plus embedding
# whatever isn't already cached, and that transient cost scales with
# count. Halving it (env-overridable) is a direct, blunt memory lever
# for a backend that's been sitting too close to Render's free-tier
# 512MB ceiling. Randomly sampled rather than just taking the first N,
# so this doesn't systematically favor whichever companies happen to
# come first in DEFAULT_COMPANIES -- every company gets roughly
# proportional representation in what's searchable, at the cost of
# only ~half of all postings being findable at any given time.
_MAX_INDEXED_POSTINGS = int(os.environ.get("MAX_INDEXED_POSTINGS", "3000"))


def _rebuild_postings_index():
    try:
        all_postings = get_all_postings(db_path=DB_PATH)
        if len(all_postings) > _MAX_INDEXED_POSTINGS:
            all_postings = random.sample(all_postings, _MAX_INDEXED_POSTINGS)
        _postings_collection["collection"] = build_postings_collection(all_postings)
        _index_status["state"] = "ready"
        _index_status["last_error"] = None
    except Exception as e:  # noqa: BLE001 - surface via the search response instead of failing silently
        _index_status["state"] = "idle"
        _index_status["last_error"] = str(e)


@app.on_event("startup")
async def _prebuild_postings_index():
    """Build the search index in a background thread at startup, so the
    first search doesn't have to wait for it. Cheap once embeddings are
    saved (it loads them from Postgres instead of re-embedding). Declared
    after _warm_embedding_model, so the model is already loaded when this
    thread starts."""
    if _index_status["state"] == "idle":
        _index_status["state"] = "building"  # so /api/companies/search doesn't schedule a duplicate build
        threading.Thread(target=_rebuild_postings_index, daemon=True).start()


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

        # Don't rebuild the search index here. Just mark the in-memory
        # index stale; /api/companies/search rebuilds it lazily on the
        # next search request. That rebuild loads saved embeddings and
        # only embeds postings that are new since the last build.
        _postings_collection["collection"] = None
        _index_status["state"] = "idle"

        # Now that every company's rows are re-inserted, drop saved
        # embeddings for postings that no longer exist. Done here (not
        # mid-refresh) so we never delete the embedding of a posting that
        # is only briefly absent while its company is being re-inserted.
        try:
            prune_posting_embeddings()
        except Exception as e:  # noqa: BLE001 - housekeeping only; never fail the refresh over it
            print(f"Pruning saved embeddings failed (non-fatal): {e}")

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
# Health check (also handy for an uptime pinger). Reports this process's own
# memory, because Render's free tier doesn't show memory charts.
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    current = peak = None
    try:
        with open("/proc/self/status") as f:  # Linux only (Render); stays null on Windows
            for line in f:
                if line.startswith("VmRSS:"):
                    current = round(int(line.split()[1]) / 1024, 1)
                elif line.startswith("VmHWM:"):
                    peak = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return {
        "status": "ok",
        "memory_mb": current,            # memory in use right now
        "peak_memory_mb": peak,          # highest since this process started
        "sessions_in_memory": len(SESSIONS),
        "postings_indexed": _postings_collection["collection"].count() if _postings_collection["collection"] else 0,
    }


# ---------------------------------------------------------------------------
# Static frontend — React SPA build (dist/), replacing the old vanilla
# multi-page frontend/ (index.html/login.html/companies.html each served by
# name). A built SPA is one index.html with client-side routing (React
# Router), so refreshing on e.g. /login or /companies now has to fall back
# to that same index.html rather than 404ing on a page-specific route --
# the classic "404 on refresh" class of bug that doesn't exist in a
# multi-page app but does in an SPA. /api/... routes are matched first
# (declared above) and still 404 normally if they don't exist; only
# everything else falls through to the SPA.
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIST_DIR = os.path.join(_PROJECT_ROOT, "dist")

# Only serve the React build if it exists. On Render (API-only, with the
# frontend on Vercel) there is no dist/ folder, so this whole block is
# skipped and the API starts normally. Locally, where you've run
# `npm run build`, it works exactly as before.
if os.path.isdir(os.path.join(_DIST_DIR, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Any path not already matched by an /api/... route above falls
        # through to here and gets the SPA shell; React Router then reads
        # the URL client-side and renders the right page (Dashboard, Login,
        # Companies) -- this is what makes /login, /companies, and a
        # refresh on either of those work instead of 404ing.
        index_path = os.path.join(_DIST_DIR, "index.html")
        if not os.path.isfile(index_path):
            raise HTTPException(
                500,
                "React build not found (dist/index.html missing) -- run "
                "'npm install && npm run build' in the project root first.",
            )
        return FileResponse(index_path)