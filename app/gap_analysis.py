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

HARD 2-MINUTE OVERALL BUDGET: earlier versions capped each individual
attempt at 60s (via request_options timeout) but let retries/backoff
stack on top of that uncapped — worst case (3 attempts x 60s + 2s +
4s backoff) could run ~186s, well past 2 minutes, and each hung/slow
call held memory (the model object, buffered response, thread state)
for that whole time. On a memory-constrained host (e.g. Render's free
tier), a few resumes each taking 3 minutes compounds fast. Fixed by
tracking real elapsed wall time against a single MAX_TOTAL_SECONDS
budget: each attempt's own timeout is clipped to whatever time is
actually left, and we never start a new attempt or backoff sleep that
would push total elapsed time past the budget. A single
generate_gap_analysis() call now cannot exceed ~120s under any
combination of retries/backoff/transient errors.
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

# Hard ceiling on total wall time for ONE generate_gap_analysis() call,
# across every attempt and every backoff sleep combined. Not just a
# per-attempt timeout -- see module docstring for why that distinction
# matters (per-attempt timeouts alone let retries stack past this).
MAX_TOTAL_SECONDS = 120


def configure_gemini(api_key: str):
    genai.configure(api_key=api_key)


def _format_sections(sections: Dict[str, str]) -> str:
    return "\n\n".join(f"[{name}]\n{text}" for name, text in sections.items())


def _is_transient(error_text: str) -> bool:
    return (
        "503" in error_text or "overloaded" in error_text or "high demand" in error_text
        or "504" in error_text or "deadline" in error_text
    )


def _is_quota(error_text: str) -> bool:
    return "429" in error_text or "quota" in error_text


def generate_gap_analysis(
    jd_sections: Dict[str, str],
    matched_resume_sections: Dict[str, str],
    model_name: str = "gemini-3.5-flash",
    max_total_seconds: int = MAX_TOTAL_SECONDS,
) -> dict:
    """Call Gemini with only the matched/grounded chunks and parse the
    structured JSON response. Falls back to a safe default dict if
    parsing fails OR if the call fails/times out, rather than crashing
    the whole ranking table.

    Timing guarantee: this function will not run for longer than
    ~max_total_seconds (default 120s / 2 minutes) wall-clock, no
    matter how many transient errors it hits. It tracks real elapsed
    time against that single budget rather than giving each retry its
    own independent timeout, so attempts/backoff can't silently stack
    past the intended cap. We deliberately do NOT retry 429/quota
    errors at all: those won't resolve by retrying within the same
    budget (the daily cap is exhausted) and retrying would just burn
    more of that already-exhausted quota for nothing -- a quota error
    fails immediately, using none of the 2-minute budget on retries.

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

    start = time.monotonic()

    def remaining() -> float:
        return max_total_seconds - (time.monotonic() - start)

    max_attempts = 3
    backoff_seconds = 2
    per_attempt_cap = 60  # a single attempt can never eat the WHOLE budget

    last_error = None
    response = None

    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        time_left = remaining()

        if time_left <= 0:
            last_error = TimeoutError(
                f"Gemini gap analysis exceeded the {max_total_seconds}s overall "
                f"budget before attempt {attempt} could start."
            )
            break

        # Never let one attempt's own timeout exceed what's actually
        # left of the overall budget -- this is what actually enforces
        # the hard cap, not just the per-attempt number.
        this_attempt_timeout = min(per_attempt_cap, time_left)

        try:
            response = model.generate_content(
                prompt,
                generation_config=GenerationConfig(temperature=0),
                request_options={"timeout": this_attempt_timeout, "retry": None},
            )
            last_error = None
            break
        except Exception as e:
            last_error = e
            err_text = str(e).lower()
            is_transient = _is_transient(err_text)
            is_quota = _is_quota(err_text)

            if is_quota or not is_transient or attempt == max_attempts:
                # quota errors, non-transient errors, or out of
                # attempts -> stop now, no point spending more budget.
                break

            backoff = backoff_seconds * attempt
            if remaining() - backoff <= 0:
                # Not enough budget left to sleep AND make another
                # attempt worth anything -- stop now instead of
                # sleeping past the cap for a retry that would get cut
                # off anyway.
                last_error = TimeoutError(
                    f"Gemini gap analysis stopped retrying: a further "
                    f"{backoff}s backoff would exceed the "
                    f"{max_total_seconds}s overall budget."
                )
                break
            time.sleep(backoff)

    if last_error is not None or response is None:
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