"""
Phase 6 — LLM gap analysis.

Grounding decision: we only pass the RETRIEVED/MATCHED chunks (the
resume sections that actually scored against the JD) into Gemini's
context — never the whole raw resume. This keeps the generated report
traceable to real retrieved evidence instead of letting the LLM free-
associate over content that wasn't actually part of the match. If a
claim in the report can't be traced to one of these chunks, that's a
groundedness failure — spot-check this manually (see README /
evaluation phase).

Structured output: we ask Gemini for JSON in a fixed schema
(strengths / gaps / fit_score / suggestions) so downstream rendering
(Streamlit table) is consistent across every resume, rather than
parsing free-form prose differently each time.
Determinism: temperature is set to 0 on the generation call below.
LLMs sample from a probability distribution over possible outputs by
default, so the same resume + JD can otherwise produce a different
fit_score or slightly different wording on every call. temperature=0
makes Gemini pick the highest-probability token at each step instead
of sampling, which makes output effectively deterministic in
practice — the same input should now reliably produce the same score,
which matters for reproducibility (e.g. re-running Phase 7 evaluation
and getting comparable numbers).
"""

import json
import os
import re
import time
from typing import Dict, List

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

GAP_ANALYSIS_PROMPT_TEMPLATE = """You are a resume-screening assistant. You will be given:
1. A job description (JD), broken into sections.
2. The MATCHED sections of a candidate's resume that were retrieved as relevant to this JD.

Only use the text provided below. Do not assume or invent experience,
skills, or qualifications that are not present in the provided resume
sections. If something isn't mentioned, treat it as a gap.

--- JOB DESCRIPTION ---
{jd_text}

--- MATCHED RESUME SECTIONS ---
{resume_text}

Respond with ONLY valid JSON (no markdown fences, no commentary),
using exactly this schema:

{{
  "fit_score": <integer 0-100>,
  "strengths": ["short bullet", "short bullet"],
  "gaps": ["short bullet", "short bullet"],
  "suggestions": ["short bullet", "short bullet"]
}}
"""


def configure_gemini(api_key: str):
    genai.configure(api_key=api_key)


def _format_sections(sections: Dict[str, str]) -> str:
    return "\n\n".join(f"[{name}]\n{text}" for name, text in sections.items())


def generate_gap_analysis(
    jd_sections: Dict[str, str],
    matched_resume_sections: Dict[str, str],
    model_name: str = "gemini-3.5-flash",
) -> dict:
    """Call Gemini with only the matched/grounded chunks and parse the
    structured JSON response. Falls back to a safe default dict if
    parsing fails, rather than crashing the whole ranking table.

    Model default note: gemini-3.5-flash's free-tier daily quota was
    observed at just 20 requests/day at one point (Google's newest
    Flash model tends to get the tightest free allowance). If you hit
    429/quota errors here, either enable billing on your Google Cloud
    project (removes the cap) or pass model_name="gemini-2.5-flash"
    at the call site for a much higher free-tier daily limit at the
    cost of a slightly older model.
    """
    prompt = GAP_ANALYSIS_PROMPT_TEMPLATE.format(
        jd_text=_format_sections(jd_sections),
        resume_text=_format_sections(matched_resume_sections),
    )

    model = genai.GenerativeModel(model_name)

    # Manual, limited retry -- for transient errors: 503 "model is
    # currently experiencing high demand", and 504 "deadline expired"
    # (our own timeout below being hit due to slow network/API
    # latency, not necessarily Google's fault). Both are usually
    # transient and worth one or two retries. We deliberately do NOT
    # retry 429/quota errors: those won't resolve by retrying (the
    # daily cap is exhausted) and retrying would just burn more of the
    # same already-exhausted quota for nothing. The SDK's own
    # automatic retry is still disabled (retry=None) so we stay in
    # full control of exactly which errors get retried and how many
    # times.
    max_attempts = 3  # 1 initial try + 2 retries, transient-errors-only
    backoff_seconds = 2
    timeout_seconds = 60  # raised from 30 -- observed real responses taking close to that under normal (non-error) conditions

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = model.generate_content(
                prompt,
                generation_config=GenerationConfig(temperature=0),
                request_options={"timeout": timeout_seconds, "retry": None},
            )
            last_error = None
            break
        except Exception as e:
            last_error = e
            err_text = str(e).lower()
            is_transient = (
                "503" in str(e) or "overloaded" in err_text or "high demand" in err_text
                or "504" in str(e) or "deadline" in err_text
            )
            is_quota = "429" in str(e) or "quota" in err_text
            if is_quota or not is_transient or attempt == max_attempts:
                # quota errors, non-transient errors, or out of retries -> stop now
                break
            time.sleep(backoff_seconds * attempt)  # 2s, then 4s

    if last_error is not None:
        return {
            "fit_score": None,
            "strengths": [],
            "gaps": [f"Gemini request failed or timed out: {last_error}"],
            "suggestions": [],
        }
    raw = response.text.strip()

    # Strip accidental markdown fences if the model adds them anyway
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {
            "fit_score": None,
            "strengths": [],
            "gaps": ["Could not parse LLM output — see raw response in logs."],
            "suggestions": [],
            "_raw": raw,
        }

    return parsed
