# Resume ↔ JD Ranking Engine

A semantic-search system that ranks a batch of resumes against a single
job description, and explains each match with a grounded, LLM-generated
gap analysis (strengths / gaps / suggestions).

**Mode:** many-resumes-vs-one-JD (ranking mode) — chosen deliberately
over single-resume-vs-JD because it's the stronger demo: it shows
ranking under noise, not just a single yes/no similarity score.

## Companies window (new)

A second tab (🏢 Companies) pulls current open postings from public
Greenhouse, Lever, Ashby, SmartRecruiters, Workable, and (best-effort)
Workday job-board APIs into a local SQLite database on refresh. Fully
free at this scale:

- **Grid view** — one card per configured company, hiring flag (🟢/🔴)
  derived purely from whether it has open postings right now. No LLM.
- **Detail view** — open roles, locations, and required skills
  extracted from posting text with a lightweight keyword vocabulary.
  Salary is shown only when the ATS posting includes it directly
  (free, no LLM) — the earlier Gemini-based salary-lookup fallback was
  removed after repeated quota issues; there is no on-demand estimate
  for postings without a listed salary.

**Six ATS integrations, not equally reliable:** Greenhouse, Lever,
Ashby, SmartRecruiters, and Workable all have genuinely free,
unauthenticated public endpoints with no meaningful rate limits at
this scale. Workday is different — there's no official public API,
just an internal JSON endpoint the careers page itself calls, which
works but sits behind Akamai bot-management (bursts can get blocked)
and requires knowing the exact tenant/region/site ahead of time (no
company-name-to-token mapping like the other five). Workday companies
are opt-in via `WORKDAY_COMPANIES_TO_TRY` in `app/companies_store.py`
rather than enabled by default.

**Role/location search bar:** a search box above the company grid lets
you search open roles across every tracked company at once. Role/title
matching is semantic (same MiniLM embedding model already used for
resume matching — "ML engineer" matches "machine learning scientist"),
while location is a plain substring filter, not semantic — searching
"Bangalore" won't return Bangkok just because they're both cities. The
index is rebuilt in memory each time postings are refreshed; no extra
API cost, no LLM call involved.

**Coverage caveat:** these six only cover companies whose careers page
is actually hosted on one of them. Google, Microsoft, Apple, Amazon,
and Meta all run fully custom in-house recruiting infrastructure with
no public API of any kind. Most large banks and many India-based
unicorns run Oracle Cloud, Taleo, iCIMS, or Darwinbox — also no public
API. See the comment above `DEFAULT_COMPANIES` in
`app/companies_store.py` for the specific list of requested companies
that don't qualify and why.

## Architecture

```
Resumes (PDF/DOCX) ─┐
                     ├─> Parse & clean ─> Section chunking ─> Embed (MiniLM)
JD (PDF/DOCX)       ─┘                                              │
                                                                     v
                                                          ChromaDB collection
                                                                     │
                                        JD chunks ──query──> max-pooled scores
                                                                     │
                                                       Length-normalized ranking
                                                                     │
                                              Gemini (grounded on matched chunks only)
                                                                     │
                                                          Streamlit results table
```

## Why semantic embeddings beat keyword matching

Keyword/TF-IDF systems score on shared surface tokens. "Python
developer" and "software engineer skilled in Python" share almost no
vocabulary beyond "Python," so a keyword-based ATS filter under-scores
a genuinely strong match. Embedding models (here, `all-MiniLM-L6-v2`)
place both phrases near each other in vector space because they were
trained to capture *meaning*, not token overlap — so paraphrased or
differently-worded qualifications still score well. This is the
concrete failure mode of naive keyword-based screening tools, and it's
the reason this project exists.

## Key design decisions

**Section-level chunking, not whole-document embedding** (Phase 2).
Whole-document embedding gives one opaque number. Section-level
chunking lets the system say "this resume matched on Experience but
not Skills" — which directly powers the gap-analysis feature and is
far more defensible in review.

**Max-pooling across chunk scores, not averaging** (Phase 4). A resume
can be a strong fit even if only one section matches extremely well;
averaging would drag a strong match down because of unrelated sections
(e.g., Education). Max-pooling mimics how a human reviewer skims for
the *best* evidence rather than forming an average impression. The
trade-off is that one lucky section in an otherwise weak resume can
inflate a score — which is why normalization exists on top of it.

**Fit score = cosine similarity × 100, shown directly** (Phase 5).
The ranking table shows the resume's actual max-pooled cosine
similarity to the JD as a 0-100 number — no batch-relative rescaling,
no length penalty. Trade-off worth stating out loud: MiniLM cosine
similarities for real text tend to cluster in a fairly narrow band
(often ~40-75), so scores across different resumes may look closer
together than a rescaled version would, and a resume with more chunks
still gets more chances at a high max-pooled score (a mild length
bias). If a Gemini key is available, an LLM-assessed score is
computed automatically for every resume right when you click "Rank
resumes" (not on a later per-resume click), and shows in the same
table next to Fit Score — a qualitative judgment from Gemini rather
than a similarity calculation, and it can legitimately disagree with
Fit Score (e.g. it can reason about eligibility criteria like
graduation year that vector similarity has no way to capture). This
means ranking a large batch with a key present makes one Gemini call
per resume up front, which is slower and uses more API quota than
generating gap analysis on demand — a deliberate trade-off for having
both scores visible immediately.

**LLM grounding** (Phase 6). Gemini only ever sees the *matched*
resume sections — never the full raw resume — so the generated report
can't hallucinate claims from parts of the resume that weren't
actually part of the retrieved evidence. Output is requested as fixed-
schema JSON (fit_score / strengths / gaps / suggestions) so every
report renders consistently in the results table. Generation runs at
`temperature=0` so the same resume/JD pair reliably produces the same
score and report — without this, Gemini's default sampling can give a
different fit_score on every re-run of identical input, which breaks
reproducibility for both the demo and the evaluation scripts below.

## Evaluation

Two separate things get evaluated — ranking quality and generation
quality — because a system can rank resumes well while still
hallucinating in its gap-analysis reports, or vice versa.

**A ready-to-run sample set is included** in `sample_data/`: a JD
(`jd_backend_engineer.txt`) and 12 synthetic resumes (`resumes/`,
4 strong / 4 medium / 4 weak fit) with a matching `labels.csv`
already filled in. These are synthetic, not real resumes, so treat
the numbers from them as "the harness works end-to-end" rather than
a real-world benchmark — swap in your own real (or anonymized) resumes
and re-label `labels.csv` for numbers that actually mean something
about your resume pool. Either way, running the two commands below
costs nothing but time and your existing free Gemini key.

**1. Ranking quality** (`evaluate.py`). 10–15 resume/JD pairs are
hand-labeled strong/medium/weak fit (`sample_data/labels.csv` is the
template, already filled in for the sample set). The script runs the
full retrieval+ranking pipeline and computes Spearman rank correlation
between the system's fit scores and the hand-labeled expected ranking.
This script does NOT call Gemini at all (embeddings + ranking only),
so it costs nothing and never hits an API quota.

```
python evaluate.py --jd sample_data/jd_backend_engineer.txt --resume_dir sample_data/resumes --labels sample_data/labels.csv
```

> Replace this line with your real numbers once you've run it, e.g.:
> *"Spearman correlation of 0.78 across 12 hand-labeled resume/JD
> pairs, indicating strong but imperfect agreement with human
> judgment — the two disagreements were both resumes with heavily
> templated formatting (see limitations)."*

**2. Generation faithfulness** (`evaluate_faithfulness.py`). This is
the automated version of "does every claim in the gap-analysis report
actually trace back to the retrieved resume context, or is the LLM
inventing things?" — the manual spot-check called out in Phase 6.
Uses [RAGAS](https://docs.ragas.io) with Gemini as the judge model
(free, reuses the same API key), scoring two metrics:

- **faithfulness** — fraction of claims in the generated report that
  are supported by the retrieved chunks. 1.0 = every claim grounded,
  lower = hallucinated content not present in the resume.
- **answer_relevancy** — whether the report actually addresses the
  fit/gap question, rather than drifting into generic career advice.

```
python evaluate_faithfulness.py \
    --jd sample_data/jd_backend_engineer.txt --resume_dir sample_data/resumes \
    --resumes strong_match_1.txt medium_match_1.txt weak_match_1.txt
```

This reads `GEMINI_API_KEY` from `.env` automatically, same as the
Streamlit app — no need to pass the key on the command line. If you'd
rather pass it explicitly (or don't have a `.env` set up), add
`--api_key YOUR_GEMINI_KEY` to the command above.

> Replace with real numbers once run, e.g.: *"Mean faithfulness of
> 0.91 across 8 generated reports — the one low-scoring report
> attributed a certification to the candidate that wasn't in the
> retrieved chunks, traced to a section-matching mismatch rather than
> pure LLM invention."*
> Note: this script does call Gemini (as the judge), so it's subject
> to whatever daily free-tier quota your chosen model has — see the
> model note in `app/gap_analysis.py` if you hit a 429.

## Known limitations

- **Heavily templated/graphic resumes** now go through a 3-stage
  fallback instead of silently mis-parsing: PyMuPDF block-sort extraction
  first, then pdfplumber (different table/layout model) if that looks
  too thin, then local OCR via Tesseract (through pytesseract) as a
  last resort for scanned/image-based PDFs. This is meaningfully
  better than before but still not perfect — OCR output can contain
  recognition errors, and the UI now flags both "very little text
  extracted" and "OCR fallback was used" so a low Fit Score can be
  read as "check this one manually" rather than "weak candidate."
  **Windows setup note:** `pytesseract` is just a Python wrapper — it
  needs the actual Tesseract OCR binary installed separately (not via
  pip) and on PATH; see
  [the Tesseract Windows install docs](https://github.com/UB-Mannheim/tesseract/wiki).
  If Tesseract isn't installed, OCR fallback silently no-ops (returns
  no extra text) rather than crashing the app.
- Section detection relies on recognizing common English headings
  (Experience, Education, Skills, etc.); resumes using unconventional
  or non-English headings fall back to a single "General" chunk with
  no section-level breakdown.
- Fit scores are **relative to the current batch**, not an absolute,
  portable measure — re-running with a different set of resumes will
  shift the 0–100 scale.
- The evaluation set (10–15 pairs) is small; correlation numbers
  should be read as directional evidence for a portfolio project, not
  a statistically rigorous benchmark. The bundled sample set is
  synthetic (see Evaluation section above) — swap in real resumes for
  numbers that mean something about your actual resume pool.
- **Companies window — Workday entries are best-effort by design.**
  There's no official public Workday API to switch to (even Workday's
  enterprise customers hit the same internal endpoint); fetches now
  retry with backoff (2s/4s/8s) on a likely Akamai block before
  surfacing a clear error, but a Workday company can still fail more
  often than the other five ATSs, especially under frequent refreshes.
- **Companies search — location matching is substring + a curated
  metro-area alias table** (`app/location_aliases.py`), not full
  semantic embedding. This is deliberate: embedding similarity would
  match "Bangalore" to "Bangkok" just because both are cities, which
  is wrong for a location filter. The alias table currently covers a
  starter set of major tech hubs (Bay Area, NCR, NYC, Bengaluru,
  etc.) — searches for a metro area not yet in that list will fall
  back to plain substring matching only.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then edit .env and paste your Gemini API key
streamlit run streamlit_app.py
```

You'll need a free Gemini API key (from Google AI Studio). Setting it
in `.env` as `GEMINI_API_KEY=...` loads it automatically — the sidebar
input only appears as a fallback if no `.env` key is found. Ranking
itself works without a key at all; only gap analysis needs one.
`.env` is gitignored so your key never gets committed.

## Deployment

Push this repo to GitHub and deploy via
[Streamlit Community Cloud](https://streamlit.io/cloud) (free tier) —
point it at `streamlit_app.py` as the entry point.

## Running the merged app (backend + frontend)

The FastAPI backend in `backend/main.py` wraps the existing `app/`
modules (parsing, chunking, embeddings, ranking, gap analysis, PDF
report, companies store) in a JSON API, and serves the static
frontend in `frontend/` (`index.html` for ranking, `companies.html`
for the companies board) — replacing `streamlit_app.py` as the UI.

Install dependencies (now includes fastapi/uvicorn/python-multipart):

    pip install -r requirements.txt

Optionally set `GEMINI_API_KEY` in a `.env` file (see `.env.example`)
so you don't have to paste a key into the form each time.

Run the server from the project root:

    uvicorn backend.main:app --reload --port 8000

Then open http://localhost:8000/ in a browser. `streamlit_app.py`
still works standalone if you'd rather use the original Streamlit UI
instead — the two are independent front ends over the same `app/`
package.
