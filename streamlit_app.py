"""
Phase 8 — UI & deployment.

Streamlit app implementing ranking mode: many resumes vs. one JD.
Run locally with:  streamlit run streamlit_app.py
Deploy for free on Streamlit Community Cloud by pointing it at this
repo + this file.
"""

import os
import hashlib
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from app.parsing import parse_document, parse_document_with_meta, is_low_extraction
from app.chunking import chunk_by_section, chunks_to_list
from app.vector_store import build_collection, score_resumes_against_jd
from app.ranking import normalize_and_rank
from app.gap_analysis import configure_gemini, generate_gap_analysis
from app.pdf_report import build_gap_analysis_pdf

st.set_page_config(page_title="Resume Ranker", layout="wide")
st.title("📄 Resume ↔ JD Ranking Engine")
st.caption(
    "Semantic ranking of multiple resumes against one job description, "
    "with section-level grounded gap analysis."
)

tab_rank, tab_companies = st.tabs(["🎯 Rank Resumes", "🏢 Companies"])

with tab_rank:
    env_key = os.environ.get("GEMINI_API_KEY")

    with st.sidebar:
        st.header("Setup")
        if env_key:
            st.success("Gemini API key loaded from environment.")
            gemini_key = env_key
        else:
            gemini_key = st.text_input("Gemini API key", type="password")
            st.caption("Tip: set GEMINI_API_KEY in a .env file to skip this field.")
        st.markdown("---")
        st.markdown(
            "**How it works:**\n"
            "1. Upload a JD and multiple resumes\n"
            "2. Text is parsed, cleaned, and split into sections\n"
            "3. Sections are embedded and matched semantically\n"
            "4. Resumes are ranked by a length-normalized fit score\n"
            "5. Gemini explains strengths/gaps, grounded in matched sections only"
        )

    col1, col2 = st.columns(2)
    with col1:
        jd_file = st.file_uploader("Job Description", type=["pdf", "docx", "txt"])
    with col2:
        resume_files = st.file_uploader(
            "Resumes (upload multiple)", type=["pdf", "docx", "txt"], accept_multiple_files=True
        )

    run_button = st.button("Rank resumes", type="primary", disabled=not (jd_file and resume_files))


    def _content_cache_key(jd_sections: dict, matched_sections: dict) -> str:
        """Hash the actual JD + matched-resume text so identical content
        (even under a different filename) reuses the same cached gap
        analysis result, instead of calling Gemini again and risking a
        different score for what is functionally the same input.
        """
        combined = "".join(f"{k}:{v}" for k, v in sorted(jd_sections.items()))
        combined += "||" + "".join(f"{k}:{v}" for k, v in sorted(matched_sections.items()))
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()


    def get_or_generate_gap_analysis(jd_sections: dict, matched_sections: dict) -> dict:
        """Cache gap analysis by content hash (session-wide), not by
        filename, so duplicate resume content always yields the same
        report even across different uploaded filenames.
        """
        if "gap_reports_by_hash" not in st.session_state:
            st.session_state["gap_reports_by_hash"] = {}

        key = _content_cache_key(jd_sections, matched_sections)
        if key not in st.session_state["gap_reports_by_hash"]:
            st.session_state["gap_reports_by_hash"][key] = generate_gap_analysis(
                jd_sections, matched_sections
            )
        return st.session_state["gap_reports_by_hash"][key]

    if run_button:
        with st.spinner("Parsing documents..."):
            jd_text = parse_document(jd_file.name, jd_file.read())
            jd_sections = chunk_by_section(jd_text)
            jd_chunks = chunks_to_list("JD", jd_sections)

            all_chunks = []
            chunk_counts = {}
            resume_section_cache = {}
            low_extraction_docs = []
            ocr_used_docs = []
            for rf in resume_files:
                parsed = parse_document_with_meta(rf.name, rf.read())
                text = parsed["text"]
                if parsed["method"] == "ocr":
                    ocr_used_docs.append(rf.name)
                if is_low_extraction(text):
                    low_extraction_docs.append(rf.name)
                sections = chunk_by_section(text)
                resume_section_cache[rf.name] = sections
                chunks = chunks_to_list(rf.name, sections)
                all_chunks.extend(chunks)
                chunk_counts[rf.name] = len(chunks)
            st.session_state["low_extraction_docs"] = low_extraction_docs
            st.session_state["ocr_used_docs"] = ocr_used_docs

        with st.spinner("Embedding and scoring (semantic match, not keyword match)..."):
            collection = build_collection(all_chunks)
            scores = score_resumes_against_jd(collection, jd_chunks)
            ranked = normalize_and_rank(scores, chunk_counts)

        st.session_state["ranked"] = ranked
        st.session_state["resume_sections"] = resume_section_cache
        st.session_state["jd_sections"] = jd_sections
        st.session_state["gap_reports"] = {}

        if gemini_key:
            configure_gemini(gemini_key)
            progress = st.progress(0.0, text="Generating LLM-assessed scores...")
            for i, r in enumerate(ranked):
                matched_sections = {
                    resume_section: resume_section_cache[r["doc_name"]].get(resume_section, "")
                    for _, (resume_section, _) in r["section_matches"].items()
                }
                try:
                    report = get_or_generate_gap_analysis(jd_sections, matched_sections)
                    st.session_state["gap_reports"][r["doc_name"]] = report
                except Exception as e:
                    st.session_state["gap_reports"][r["doc_name"]] = {
                        "fit_score": None,
                        "strengths": [],
                        "gaps": [f"Gap analysis failed: {e}"],
                        "suggestions": [],
                    }
                progress.progress((i + 1) / len(ranked), text=f"Scored {i + 1}/{len(ranked)} resumes")
            progress.empty()
        else:
            st.info("Add a Gemini API key (sidebar or .env) to get LLM-assessed scores automatically.")

    if "ranked" in st.session_state:
        ranked = st.session_state["ranked"]

        st.subheader("Ranking results")
        st.caption(
            "Fit Score = embedding-based cosine similarity to the JD (0-100). "
            "LLM Score = Gemini's independent judgment, computed automatically "
            "for every resume when you rank. The two can genuinely disagree "
            "since one measures vector similarity and the other reasons about "
            "the match."
        )
        gap_reports = st.session_state.get("gap_reports", {})

        def _llm_score_display(doc_name):
            if doc_name not in gap_reports:
                return "—"
            score = gap_reports[doc_name].get("fit_score")
            return score if score is not None else "—"

        # Strong / Average / Weak summary.
        #
        # Categorization uses LLM Score when one is available, and
        # only falls back to Fit Score (cosine similarity) when there's
        # no Gemini key. Why: cosine similarity measures textual/topical
        # overlap with the JD, not actual candidate fit -- it can't
        # tell "irrelevant resume" apart from "overqualified resume
        # using different vocabulary than a fresher JD," both of which
        # show up as low similarity. The LLM Score reasons about fit
        # directly, so it's the better signal for this label whenever
        # it's available. Same thresholds (app/ranking.py) are reused
        # since LLM Score is also 0-100.
        from app.ranking import STRONG_MATCH_THRESHOLD, WEAK_MATCH_THRESHOLD, classify_match

        def _effective_category(r):
            llm_score = gap_reports.get(r["doc_name"], {}).get("fit_score")
            if llm_score is not None:
                return classify_match(llm_score)
            return r["match_category"]  # fallback: Fit Score-based, no Gemini key

        counts = {"Strong": 0, "Average": 0, "Weak": 0}
        for r in ranked:
            counts[_effective_category(r)] += 1

        st.caption(
            f"Strong ≥ {STRONG_MATCH_THRESHOLD} · Average {WEAK_MATCH_THRESHOLD}–{STRONG_MATCH_THRESHOLD} · "
            f"Weak below {WEAK_MATCH_THRESHOLD}, based on LLM Score when available "
            "(falls back to Fit Score if no Gemini key). LLM Score is used "
            "preferentially because cosine similarity alone can't distinguish "
            "an irrelevant resume from an overqualified one using different "
            "wording than the JD. Thresholds are a starting point (see "
            "ranking.py), not a scientifically calibrated cutoff."
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("🟢 Strong match", counts["Strong"])
        m2.metric("🟡 Average match", counts["Average"])
        m3.metric("🔴 Weak match", counts["Weak"])

        table_df = pd.DataFrame([
            {
                "Resume": r["doc_name"],
                "Fit Score": r["fit_score"],
                "Match": _effective_category(r),
                "LLM Score": _llm_score_display(r["doc_name"]),
            }
            for r in ranked
        ])
        st.dataframe(table_df, use_container_width=True, hide_index=True)

        low_extraction_docs = st.session_state.get("low_extraction_docs", [])
        if low_extraction_docs:
            st.warning(
                "⚠️ Very little text was extracted from: "
                f"{', '.join(low_extraction_docs)}. A low Fit Score for these "
                "may reflect a parsing issue (heavily graphic/multi-column "
                "layout, or a scanned/image-based PDF) rather than a weak "
                "match — worth opening the file manually to confirm."
            )

        ocr_used_docs = st.session_state.get("ocr_used_docs", [])
        if ocr_used_docs:
            st.info(
                "🔎 OCR fallback was used for: "
                f"{', '.join(ocr_used_docs)} (the normal text extraction "
                "returned too little text, likely an image-based/scanned "
                "PDF or a heavily graphic template). OCR text can contain "
                "recognition errors — worth a quick manual check for these."
            )

        st.subheader("Gap analysis (Gemini, grounded in matched sections)")
        if not gemini_key:
            st.info("Add a Gemini API key (sidebar or .env) to see gap analysis reports.")
        else:
            configure_gemini(gemini_key)
            if "gap_reports" not in st.session_state:
                st.session_state["gap_reports"] = {}

            just_regenerated = st.session_state.pop("just_regenerated", None)

            for r in ranked:
                has_report = r["doc_name"] in st.session_state["gap_reports"]
                force_open = r["doc_name"] == just_regenerated
                with st.expander(f"{r['doc_name']} — fit score {r['fit_score']} ({_effective_category(r)})", expanded=force_open):
                    st.markdown("**Section matches (resume section ↔ JD section, similarity):**")
                    for jd_section, (resume_section, sim) in r["section_matches"].items():
                        st.write(f"- JD `{jd_section}` ↔ Resume `{resume_section}` (sim={sim:.3f})")

                    button_label = (
                        f"Regenerate gap analysis for {r['doc_name']}" if has_report
                        else f"Generate gap analysis for {r['doc_name']}"
                    )
                    if st.button(button_label, key=f"gap_{r['doc_name']}"):
                        matched_sections = {
                            resume_section: st.session_state["resume_sections"][r["doc_name"]].get(resume_section, "")
                            for _, (resume_section, _) in r["section_matches"].items()
                        }
                        with st.spinner("Calling Gemini..."):
                            try:
                                report = generate_gap_analysis(
                                    st.session_state["jd_sections"], matched_sections
                                )
                                st.session_state["gap_reports"][r["doc_name"]] = report
                                # keep the content-hash cache in sync so future
                                # automatic lookups for identical content reuse
                                # this fresh result instead of the stale one
                                key = _content_cache_key(st.session_state["jd_sections"], matched_sections)
                                st.session_state.setdefault("gap_reports_by_hash", {})[key] = report
                                st.session_state["just_regenerated"] = r["doc_name"]
                                st.rerun()
                            except Exception as e:
                                st.error(f"Gap analysis failed: {e}")

                    if r["doc_name"] in st.session_state["gap_reports"]:
                        report = st.session_state["gap_reports"][r["doc_name"]]

                        if report.get("fit_score") is not None:
                            st.metric("LLM-assessed fit score", f"{report['fit_score']}/100")

                        strengths = report.get("strengths", [])
                        if strengths:
                            st.markdown("**✅ Strengths**")
                            for s in strengths:
                                st.markdown(f"- {s}")

                        gaps = report.get("gaps", [])
                        if gaps:
                            st.markdown("**⚠️ Gaps**")
                            for g in gaps:
                                st.markdown(f"- {g}")

                        suggestions = report.get("suggestions", [])
                        if suggestions:
                            st.markdown("**💡 Suggestions**")
                            for sug in suggestions:
                                st.markdown(f"- {sug}")

                        if report.get("_raw"):
                            with st.expander("Raw LLM output (parsing failed)"):
                                st.code(report["_raw"])

                        if "gap_pdfs" not in st.session_state:
                            st.session_state["gap_pdfs"] = {}
                        if r["doc_name"] not in st.session_state["gap_pdfs"]:
                            try:
                                st.session_state["gap_pdfs"][r["doc_name"]] = build_gap_analysis_pdf(
                                    resume_name=r["doc_name"],
                                    fit_score=r["fit_score"],
                                    llm_report=report,
                                )
                            except Exception as e:
                                st.error(f"PDF generation failed: {e}")

                        if r["doc_name"] in st.session_state["gap_pdfs"]:
                            col_a, col_b = st.columns(2)
                            with col_a:
                                st.download_button(
                                    label="📄 Download PDF (browser)",
                                    data=st.session_state["gap_pdfs"][r["doc_name"]],
                                    file_name=f"gap_analysis_{r['doc_name'].rsplit('.', 1)[0]}.pdf",
                                    mime="application/pdf",
                                    key=f"pdf_{r['doc_name']}",
                                )
                            with col_b:
                                if st.button("💾 Save to disk instead", key=f"savepdf_{r['doc_name']}"):
                                    reports_dir = os.path.join(os.getcwd(), "generated_reports")
                                    os.makedirs(reports_dir, exist_ok=True)
                                    out_path = os.path.join(
                                        reports_dir,
                                        f"gap_analysis_{r['doc_name'].rsplit('.', 1)[0]}.pdf",
                                    )
                                    with open(out_path, "wb") as f:
                                        f.write(st.session_state["gap_pdfs"][r["doc_name"]])
                                    st.success(f"Saved to: {out_path}")

with tab_companies:
    from app.companies_store import (
        DEFAULT_COMPANIES, DB_PATH, refresh_all,
        get_companies_overview, get_postings_for_company, get_all_postings,
    )
    from app.postings_search import build_postings_collection, search_postings

    st.subheader("Companies")
    st.caption(
        "Open postings pulled from each company's public job board and "
        "cached in a local database — no LLM calls just to browse. "
        "🟢 = has open postings right now, 🔴 = none."
    )

    def _rebuild_postings_index():
        st.session_state["postings_collection"] = build_postings_collection(
            get_all_postings(db_path=DB_PATH)
        )

    # Auto-refresh once per session on first load of this tab, so the
    # hiring flags are accurate immediately instead of showing stale
    # 🔴 (empty DB) until the user manually clicks Refresh. Guarded by
    # a session_state flag so it only fires once, not on every rerun
    # (Streamlit reruns the whole script on every widget interaction).
    if "companies_auto_refreshed" not in st.session_state:
        with st.spinner("Loading current postings..."):
            results = refresh_all(DEFAULT_COMPANIES, db_path=DB_PATH)
            _rebuild_postings_index()
        st.session_state["companies_refresh_results"] = results
        st.session_state["companies_auto_refreshed"] = True

    col_refresh, col_info = st.columns([1, 3])
    with col_refresh:
        if st.button("🔄 Refresh postings"):
            with st.spinner("Pulling current postings..."):
                results = refresh_all(DEFAULT_COMPANIES, db_path=DB_PATH)
                _rebuild_postings_index()
            st.session_state["companies_refresh_results"] = results
            st.session_state.pop("companies_selected", None)

    if "companies_refresh_results" in st.session_state:
        failures = {k: v[1] for k, v in st.session_state["companies_refresh_results"].items() if not v[0]}
        if failures:
            st.warning(f"Some companies failed to refresh: {failures}")

    st.markdown("---")
    st.markdown("**Search roles across all companies**")
    st.caption(
        "Role/title matching is semantic (finds \"ML engineer\" when you "
        "search \"machine learning\"). Location is an exact-ish filter, "
        "not semantic — searching \"Bangalore\" won't return Bangkok."
    )
    search_col1, search_col2, search_col3 = st.columns([2, 1, 1])
    with search_col1:
        role_query = st.text_input("Role / keywords", placeholder="e.g. backend engineer, product designer")
    with search_col2:
        location_filter = st.text_input("Location filter (optional)", placeholder="e.g. Bangalore, Remote")
    with search_col3:
        st.write("")
        st.write("")
        search_clicked = st.button("🔍 Search")

    if search_clicked and (role_query.strip() or location_filter.strip()):
        collection = st.session_state.get("postings_collection")
        if collection is None or collection.count() == 0:
            st.info("No postings indexed yet — click **Refresh postings** first.")
        else:
            search_result = search_postings(collection, role_query, location_filter, top_k=100)
            matches = search_result["matches"]
            total = search_result["total"]
            if not matches:
                st.info("No matching roles found. Try a broader role query or clear the location filter.")
            else:
                if total > len(matches):
                    st.caption(f"Showing {len(matches)} of {total} matches — narrow your search to see fewer, more relevant results.")
                else:
                    st.caption(f"{total} matching posting(s).")
                for m in matches:
                    match_suffix = f" (match {m['similarity']:.2f})" if m["similarity"] is not None else ""
                    st.markdown(
                        f"**[{m['title']}]({m['url']})** — {m['company']} · "
                        f"{m['location'] or 'location not listed'}"
                        f"{match_suffix}"
                    )
                    if m["salary_text"]:
                        st.caption(f"💰 {m['salary_text']}")
    st.markdown("---")

    overview = get_companies_overview(db_path=DB_PATH)

    if not overview or all(c["open_postings"] == 0 for c in overview):
        st.info("No postings loaded yet — click **Refresh postings** to pull current data.")

    grid_cols = st.columns(3)
    for i, c in enumerate(overview):
        with grid_cols[i % 3]:
            flag = "🟢" if c["hiring"] else "🔴"
            st.markdown(f"### {flag} {c['name']}")
            st.caption(f"{c['open_postings']} open posting(s)")
            if st.button("View roles", key=f"view_{c['name']}", disabled=c["open_postings"] == 0):
                st.session_state["companies_selected"] = c["name"]

    selected = st.session_state.get("companies_selected")
    if selected:
        st.markdown("---")
        st.subheader(f"{selected} — open roles")
        postings = get_postings_for_company(selected, db_path=DB_PATH)

        for p in postings:
            with st.expander(f"{p['title']} — {p['location'] or 'location not listed'}"):
                st.markdown(f"[View posting]({p['url']})")

                if p["skills"]:
                    st.markdown("**Required skills (extracted from posting text):**")
                    st.write(", ".join(p["skills"]))
                else:
                    st.caption("No skills matched from the vocabulary for this posting.")

                if p["salary_text"]:
                    st.markdown("**Salary range**")
                    st.write(f"💰 {p['salary_text']}  \n*Listed directly on the job posting.*")
