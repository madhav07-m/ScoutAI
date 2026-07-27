"""
Companies window — Phase 9.

Data source: six ATS public/free job-board APIs.

    Greenhouse:     https://boards-api.greenhouse.io/v1/boards/{token}/jobs
    Lever:          https://api.lever.co/v0/postings/{token}?mode=json
    Ashby:          https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true
    SmartRecruiters: https://api.smartrecruiters.com/v1/companies/{token}/postings
    Workable:       https://apply.workable.com/api/v1/widget/accounts/{token}
    Workday:        https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs (POST)

The first four are genuinely free and unauthenticated with no per-call
quota that matters at this scale. Workday is different: it has no
official public API, but every public-facing Workday careers site
does expose this JSON endpoint under the hood (same one the page
itself calls to render results) with no auth required. Two caveats
make it meaningfully less reliable than the other five:
  1. Workday sits behind Akamai bot-management; a burst of requests
     from one IP can get rate-limited/blocked, so refreshes should
     stay infrequent and not run many Workday companies back-to-back.
  2. There's no discovery mechanism -- you have to already know the
     tenant, wdN region number, and site slug (all three vary per
     company and aren't guessable from the company name), unlike
     Greenhouse/Lever/Ashby/SmartRecruiters/Workable where the token
     is just the company's own slug.
Because of this, Workday entries in DEFAULT_COMPANIES are opt-in and
best-effort -- expect them to be more failure-prone on refresh than
the other five ATSs, and don't be surprised if one needs its
tenant/site corrected after Workday changes something on their end.

We store results in our own SQLite DB (free, file-based, zero infra)
so the UI never has to call out to any ATS on every render — only a
periodic refresh job does that. "Hiring flag" is fully derived (open
postings right now = green, none = red) — no LLM involved in this
window at all.

Required skills per posting are extracted with the SAME embedding /
keyword pipeline already built for resume parsing — no extra API cost.

IMPORTANT — on the company list: tokens below are the company's slug
in the ATS's own public URL (e.g. boards.greenhouse.io/stripe ->
"stripe") and are ONLY valid if that company's careers page is
actually hosted on that ATS. Most large enterprises (banks, most of
FAANG, and many India-based unicorns) run on Oracle Cloud, Taleo,
iCIMS, Darwinbox, or a fully custom career site — none of which
expose an open, unauthenticated job API at all, on any of the six
ATSs above. Guessing a token for a company not actually on one of
these six just produces a 404 at refresh time. Only add a company
once you've confirmed its careers URL actually matches one of the six
patterns above.
"""

import re
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

DB_PATH = "companies.db"

# board_type: "greenhouse", "lever", or "ashby". token is the
# company's slug in that ATS's public URL.
DEFAULT_COMPANIES = [
    {"name": "Stripe", "board_type": "greenhouse", "token": "stripe"},
    {"name": "Airbnb", "board_type": "greenhouse", "token": "airbnb"},
    {"name": "Figma", "board_type": "greenhouse", "token": "figma"},
    {"name": "InMobi", "board_type": "greenhouse", "token": "inmobi"},
    {"name": "Spotify", "board_type": "lever", "token": "spotify"},
    {"name": "Netflix", "board_type": "lever", "token": "netflix"},
    {"name": "Atlassian", "board_type": "lever", "token": "atlassian"},
    {"name": "Robinhood", "board_type": "greenhouse", "token": "robinhood"},
    {"name": "Plaid", "board_type": "lever", "token": "plaid"},
    {"name": "OpenAI", "board_type": "ashby", "token": "openai"},
    {"name": "Anthropic", "board_type": "greenhouse", "token": "anthropic"},
    {"name": "Cohere", "board_type": "ashby", "token": "cohere"},
    {"name": "Adyen", "board_type": "greenhouse", "token": "adyen"},
    {"name": "Block", "board_type": "greenhouse", "token": "block"},
    {"name": "Groww", "board_type": "greenhouse", "token": "groww"},
    {"name": "Jane Street", "board_type": "greenhouse", "token": "janestreet"},
    {"name": "DRW", "board_type": "greenhouse", "token": "drweng"},
    {"name": "Optiver", "board_type": "greenhouse", "token": "optiverprivate"},
    {"name": "Databricks", "board_type": "greenhouse", "token": "databricks"},
    {"name": "Cloudflare", "board_type": "greenhouse", "token": "cloudflare"},
    {"name": "MongoDB", "board_type": "greenhouse", "token": "mongodb"},
    {"name": "Dropbox", "board_type": "greenhouse", "token": "dropbox"},
    {"name": "Palantir", "board_type": "lever", "token": "palantir"},
    {"name": "Visa", "board_type": "smartrecruiters", "token": "Visa"},
    {"name": "Hugging Face", "board_type": "workable", "token": "huggingface"},
    {"name": "Riot Games", "board_type": "greenhouse", "token": "riotgamesinc"},
    {"name": "Roblox", "board_type": "greenhouse", "token": "roblox"},
    {"name": "Discord", "board_type": "greenhouse", "token": "discord"},
    {"name": "Postman", "board_type": "greenhouse", "token": "postman"},
    {"name": "Razorpay", "board_type": "greenhouse", "token": "razorpaysoftwareprivatelimited"},
    {"name": "Meesho", "board_type": "lever", "token": "meesho"},
    {"name": "Niantic Spatial", "board_type": "ashby", "token": "niantic-spatial"},
]

# Companies confirmed on Workday but NOT enabled by default (see the
# module docstring re: Workday's fragility -- bot-blocking, no
# discovery mechanism). Uncomment / add to DEFAULT_COMPANIES above if
# you want to try them; they're more likely to need occasional
# tenant/site correction than the other five ATSs.
#   NVIDIA: tenant="nvidia", region="wd5", site="NVIDIAExternalCareerSite"
#   Epic Games: tenant="epicgames", region="wd5", site="Epic_Games"
#   BrowserStack: tenant="browserstack", region="wd3", site="External"
# WORKDAY_COMPANIES_TO_TRY = [
#     {"name": "NVIDIA", "board_type": "workday", "tenant": "nvidia",
#      "region": "wd5", "site": "NVIDIAExternalCareerSite"},
#     {"name": "Epic Games", "board_type": "workday", "tenant": "epicgames",
#      "region": "wd5", "site": "Epic_Games"},
#     {"name": "BrowserStack", "board_type": "workday", "tenant": "browserstack",
#      "region": "wd3", "site": "External"},
# ]

#   - Unity: was on greenhouse (token "unity3d") and confirmed live
#     when added, but the board consistently 404'd across multiple
#     refreshes afterward -- job-boards.greenhouse.io/unity3d now
#     shows an error state. Removed from DEFAULT_COMPANIES. If
#     re-adding, confirm Unity has a current job board token live
#     (they may have moved off Greenhouse, or just turned the board
#     off temporarily) rather than reusing "unity3d" blind.
# NOT included above (requested but not verified to be on a free,
# unauthenticated ATS API as of this writing):
#   - Google, Microsoft, Apple, Amazon, Meta: all run fully custom,
#     in-house recruiting infrastructure -- confirmed no public
#     Greenhouse/Lever/Ashby board exists for any of them.
#   - NVIDIA, Adobe, Oracle, Salesforce, Uber, LinkedIn, Cisco,
#     VMware, ServiceNow, Snowflake, Redis: large enough that most
#     likely run Workday/Taleo/iCIMS or a custom portal; not
#     confirmed on Greenhouse/Lever/Ashby, so left out rather than
#     guessed. (Salesforce specifically already appeared as a company
#     that just USES Greenhouse as a customer of its OWN product in
#     unrelated search results -- not the same as Salesforce's own
#     careers page being Greenhouse-hosted.)
#   - Mistral AI, Hugging Face: Hugging Face's public board is on
#     Workable, a fourth ATS this app doesn't support; Mistral AI not
#     confirmed on any of the three.
#   - Hudson River Trading, Tower Research Capital, Quadeye, IMC
#     Trading, Jump Trading, Citadel Securities, Two Sigma: not
#     confirmed with a specific working token (some, e.g. Tower, may
#     be on Greenhouse but the exact slug wasn't confirmed live).
#   - Wise, Revolut, Klarna, PayPal, PhonePe, Paytm, CRED, Zerodha,
#     Juspay, BharatPe, Pine Labs, Fi Money: not confirmed. (Razorpay
#     WAS in this bucket but is now CONFIRMED greenhouse, token
#     "razorpaysoftwareprivatelimited" -- added to DEFAULT_COMPANIES
#     above. Worth re-checking the others in this line too, since
#     Razorpay being wrong suggests some of these may also have
#     migrated onto a supported ATS since they were last checked.)
#   - JPMorgan Chase, Goldman Sachs, Morgan Stanley, American Express,
#     Mastercard, Capital One: large banks, overwhelmingly on Workday
#     or Oracle Cloud recruiting (JPMorgan confirmed on Oracle Cloud).
#     Visa was flagged in one source as "the most prominent tech
#     company on Ashby" but a specific working token wasn't verified.
#   - Zepto, Swiggy, Zomato, Flipkart, Ola: not confirmed on
#     Greenhouse/Lever/Ashby; large Indian companies commonly use
#     Darwinbox or an in-house ATS instead. (Meesho WAS in this
#     bucket but is now CONFIRMED lever, token "meesho" -- added to
#     DEFAULT_COMPANIES above. Same lesson as Razorpay: worth
#     re-checking the rest of this line too.)
#   - BrowserStack, Postman, Chargebee, Hasura, Appsmith, DevRev,
#     InVideo, Rocketlane, Sprinto, Atomicwork, Observe.AI, Exotel,
#     Darwinbox, Slice, Jupiter, Pocket FM, ShareChat, Gupshup,
#     Yellow.ai, Unacademy, Scaler, Nykaa Tech, CoinDCX, CoinSwitch,
#     Licious, Whatfix, HackerRank, LambdaTest: requested; checked
#     individually where noted below, rest still unconfirmed.
#       - Postman: CONFIRMED greenhouse, token "postman" -- added above.
#       - BrowserStack: CONFIRMED workday, tenant "browserstack",
#         region "wd3", site "External" -- added to
#         WORKDAY_COMPANIES_TO_TRY above.
#       - Chargebee: confirmed NOT on any of the 6 -- careers page
#         (chargebee.com/careers/join-us/) routes to LinkedIn Jobs
#         and a custom portal at jobs.chargebee.com.
#       - Hasura (rebranded PromptQL): confirmed NOT on any of the 6
#         -- careers page (promptql.io/careers, formerly
#         hasura.io/careers) routes to jobs.gem.com/promptql, a 7th
#         ATS ("Gem") this app doesn't support.
#       - Appsmith: confirmed NOT on any of the 6 -- custom career
#         page at appsmith.com/careers, no ATS host detected, and
#         had zero open roles at time of check anyway.
#       - DevRev and the rest: not yet checked -- searches returned
#         only recruiting-aggregator noise (Instahyre, Wellfound,
#         BuiltIn, etc.), not a confirmed ATS host. Needs a direct
#         fetch of each company's own /careers page to find the real
#         "View open roles" link and read off its ATS, the same way
#         Chargebee/Hasura/Appsmith were resolved above.
# If you confirm any of these is actually on Greenhouse/Lever/Ashby
# (check whether its careers page URL is boards.greenhouse.io/...,
# jobs.lever.co/..., or jobs.ashbyhq.com/...), add it to
# DEFAULT_COMPANIES above with board_type set accordingly.

# Lightweight, dependency-free skill vocabulary for extracting
# "required skills" out of raw posting text. Deliberately simple
# (regex/keyword match) rather than another embedding pass, since
# this needs to run over every posting on every refresh cheaply.
SKILL_VOCAB = [
    "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "C++", "C#",
    "SQL", "React", "Vue", "Angular", "Node.js", "Django", "Flask",
    "AWS", "GCP", "Azure", "Kubernetes", "Docker", "Terraform",
    "Machine Learning", "Deep Learning", "NLP", "PyTorch", "TensorFlow",
    "REST", "GraphQL", "Kafka", "Spark", "Airflow", "CI/CD",
    "Product Management", "Figma", "SEO", "A/B Testing",
]


@dataclass
class Posting:
    id: str
    company: str
    title: str
    location: str
    url: str
    salary_text: Optional[str]
    skills: List[str]


def init_db(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS postings (
            id TEXT PRIMARY KEY,
            company TEXT,
            title TEXT,
            location TEXT,
            url TEXT,
            salary_text TEXT,
            skills TEXT,
            fetched_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS salary_cache (
            content_hash TEXT PRIMARY KEY,
            result TEXT,
            created_at REAL
        )
    """)
    conn.commit()
    return conn


def extract_skills(text: str) -> List[str]:
    found = []
    low = text.lower()
    for skill in SKILL_VOCAB:
        if skill.lower() in low:
            found.append(skill)
    return found


def _extract_salary(text: str) -> Optional[str]:
    """Some Greenhouse/Lever postings embed a salary range directly in
    the posting body/metadata. Cheap regex check before ever falling
    back to a paid/quota-limited Gemini call.
    """
    match = re.search(
        r"\$[\d,]{2,}(?:\s?[kK])?\s*(?:-|to|–)\s*\$?[\d,]{2,}(?:\s?[kK])?",
        text,
    )
    return match.group(0) if match else None


def fetch_greenhouse(token: str, company: str) -> List[Posting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    postings = []
    for job in data.get("jobs", []):
        content = job.get("content", "") or ""
        postings.append(Posting(
            id=f"gh_{token}_{job['id']}",
            company=company,
            title=job.get("title", ""),
            location=(job.get("location") or {}).get("name", ""),
            url=job.get("absolute_url", ""),
            salary_text=_extract_salary(content),
            skills=extract_skills(job.get("title", "") + " " + content),
        ))
    return postings


def fetch_lever(token: str, company: str) -> List[Posting]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    postings = []
    for job in data:
        desc = (job.get("descriptionPlain") or job.get("description") or "")
        # Each entry in "lists" (e.g. "What You'll Do", "Who You Are")
        # is USUALLY a dict with a "content" field that is an HTML
        # STRING (not a list of {text: ...} dicts) -- but some Lever
        # tenants (e.g. Spotify, Palantir) return raw strings directly
        # as list entries instead of {content: ...} dicts. Handle both
        # shapes defensively.
        raw_lists = job.get("lists") or []
        list_parts = []
        for lst in raw_lists:
            if isinstance(lst, dict):
                list_parts.append(lst.get("content", "") or "")
            elif isinstance(lst, str):
                list_parts.append(lst)
        lists_text = " ".join(list_parts)
        full_text = desc + " " + lists_text
        postings.append(Posting(
            id=f"lv_{token}_{job['id']}",
            company=company,
            title=job.get("text", ""),
            location=(job.get("categories") or {}).get("location", ""),
            url=job.get("hostedUrl", ""),
            salary_text=_extract_salary(full_text),
            skills=extract_skills(job.get("text", "") + " " + full_text),
        ))
    return postings


def fetch_ashby(token: str, company: str) -> List[Posting]:
    """Ashby's public posting-api endpoint. No key required. Salary
    (compensation) is included when the employer has chosen to
    publish it, via includeCompensation=true.
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    postings = []
    for job in data.get("jobs", []):
        desc = job.get("descriptionPlain") or ""
        comp = job.get("compensation") or {}
        salary_text = comp.get("compensationTierSummary") or _extract_salary(desc)
        loc = job.get("location") or job.get("locationName") or ""
        postings.append(Posting(
            id=f"ab_{token}_{job.get('id')}",
            company=company,
            title=job.get("title", ""),
            location=loc,
            url=job.get("jobUrl", ""),
            salary_text=salary_text,
            skills=extract_skills(job.get("title", "") + " " + desc),
        ))
    return postings


def fetch_smartrecruiters(company_id: str, company: str) -> List[Posting]:
    """SmartRecruiters public Postings API. No auth for companies that
    have the public feed enabled (most do). Paginated at 100/page;
    we loop until totalFound is exhausted.
    """
    postings = []
    offset = 0
    limit = 100
    while True:
        url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
        resp = requests.get(url, params={"limit": limit, "offset": offset}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [])
        for job in content:
            title = job.get("name", "")
            loc = (job.get("location") or {}).get("city", "")
            job_id = job.get("id", "")
            postings.append(Posting(
                id=f"sr_{company_id}_{job_id}",
                company=company,
                title=title,
                location=loc,
                url=f"https://jobs.smartrecruiters.com/{company_id}/{job_id}",
                salary_text=None,  # requires a second detail-endpoint call per job; skipped for refresh cost
                skills=extract_skills(title),
            ))
        offset += limit
        if offset >= data.get("totalFound", 0) or not content:
            break
    return postings


def fetch_workable(account_slug: str, company: str) -> List[Posting]:
    """Workable's public widget endpoint. No auth required. Filtering
    isn't supported by this endpoint, so we get every published job
    in one call.
    """
    url = f"https://apply.workable.com/api/v1/widget/accounts/{account_slug}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    postings = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        loc = job.get("location", {})
        loc_str = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")])) \
            if isinstance(loc, dict) else (loc or "")
        postings.append(Posting(
            id=f"wk_{account_slug}_{job.get('shortcode', title)}",
            company=company,
            title=title,
            location=loc_str,
            url=job.get("url", ""),
            salary_text=None,
            skills=extract_skills(title),
        ))
    return postings


def fetch_workday(tenant: str, region: str, site: str, company: str) -> List[Posting]:
    """Best-effort Workday fetch. There's no official public API --
    this hits the same internal JSON endpoint the Workday careers page
    itself calls. Two things make this less reliable than the other
    ATSs here (see module docstring): Akamai bot-management can block
    bursts of requests, and tenant/region/site have to be known ahead
    of time rather than derived from the company name. Keep refreshes
    of Workday companies infrequent.

    Retries up to 3 times with exponential backoff (2s, 4s, 8s) on a
    likely Akamai block (403, or 429) or a transient network error,
    since these are often momentary rather than a permanently dead
    tenant/site. If all retries are exhausted, raises a clear,
    specific error identifying Akamai-blocking as the likely cause
    (rather than surfacing a raw requests exception that looks like a
    generic network failure) so a person reading the refresh-failure
    banner knows what's actually going on and that retrying later
    (not fixing the token) is the right move.
    """
    url = f"https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    last_error = None

    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            if resp.status_code in (403, 429):
                last_error = (
                    f"Workday returned {resp.status_code} (likely Akamai "
                    f"bot-management blocking this request, not an invalid "
                    f"tenant/site) on attempt {attempt + 1}/3"
                )
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))  # 2s, 4s
                    continue
                raise RuntimeError(
                    f"Workday fetch for '{company}' was blocked after 3 attempts "
                    f"(HTTP {resp.status_code}). This is very likely Akamai "
                    f"bot-management rate-limiting this IP, not a wrong "
                    f"tenant/region/site -- wait a while before refreshing "
                    f"this company again, and avoid refreshing many Workday "
                    f"companies back-to-back."
                )
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            last_error = str(e)
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(
                f"Workday fetch for '{company}' failed after 3 attempts due to "
                f"a network error ({last_error}). This may be transient -- "
                f"try refreshing again shortly."
            )
    else:
        raise RuntimeError(f"Workday fetch for '{company}' failed: {last_error}")

    postings = []
    for job in data.get("jobPostings", []):
        title = job.get("title", "")
        loc = job.get("locationsText", "") or job.get("bulletFields", [""])[0]
        path = job.get("externalPath", "")
        postings.append(Posting(
            id=f"wd_{tenant}_{path}",
            company=company,
            title=title,
            location=loc,
            url=f"https://{tenant}.{region}.myworkdayjobs.com/{site}{path}",
            salary_text=None,
            skills=extract_skills(title),
        ))
    return postings


def refresh_company(company_cfg: dict, db_path: str = DB_PATH) -> int:
    """Fetch current postings for one company and upsert into SQLite.
    Returns the number of postings stored (0 = no open postings, i.e.
    the hiring flag for this company should render red).
    """
    conn = init_db(db_path)
    try:
        board_type = company_cfg["board_type"]
        if board_type == "greenhouse":
            postings = fetch_greenhouse(company_cfg["token"], company_cfg["name"])
        elif board_type == "lever":
            postings = fetch_lever(company_cfg["token"], company_cfg["name"])
        elif board_type == "ashby":
            postings = fetch_ashby(company_cfg["token"], company_cfg["name"])
        elif board_type == "smartrecruiters":
            postings = fetch_smartrecruiters(company_cfg["token"], company_cfg["name"])
        elif board_type == "workable":
            postings = fetch_workable(company_cfg["token"], company_cfg["name"])
        elif board_type == "workday":
            postings = fetch_workday(
                company_cfg["tenant"], company_cfg["region"],
                company_cfg["site"], company_cfg["name"],
            )
        else:
            raise ValueError(f"Unknown board_type: {board_type}")

        conn.execute("DELETE FROM postings WHERE company = ?", (company_cfg["name"],))
        now = time.time()
        for p in postings:
            conn.execute(
                "INSERT OR REPLACE INTO postings VALUES (?,?,?,?,?,?,?,?)",
                (p.id, p.company, p.title, p.location, p.url,
                 p.salary_text, ",".join(p.skills), now),
            )
        conn.commit()
        return len(postings)
    finally:
        conn.close()


def refresh_all(companies: List[dict] = None, db_path: str = DB_PATH) -> dict:
    """Refresh every configured company. Returns {company_name: (ok, count_or_error)}.
    A single company failing (bad token, ATS down) never blocks the others.
    """
    companies = companies or DEFAULT_COMPANIES
    results = {}
    for cfg in companies:
        try:
            count = refresh_company(cfg, db_path=db_path)
            results[cfg["name"]] = (True, count)
        except Exception as e:
            results[cfg["name"]] = (False, str(e))
    return results


def get_companies_overview(db_path: str = DB_PATH) -> List[dict]:
    """One row per company: name, open-postings count, hiring flag."""
    conn = init_db(db_path)
    try:
        rows = conn.execute(
            "SELECT company, COUNT(*) FROM postings GROUP BY company"
        ).fetchall()
        counts = {name: n for name, n in rows}
        overview = []
        for cfg in DEFAULT_COMPANIES:
            n = counts.get(cfg["name"], 0)
            overview.append({
                "name": cfg["name"],
                "open_postings": n,
                "hiring": n > 0,
            })
        return overview
    finally:
        conn.close()


def get_postings_for_company(company: str, db_path: str = DB_PATH) -> List[dict]:
    conn = init_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, location, url, salary_text, skills FROM postings WHERE company = ?",
            (company,),
        ).fetchall()
        return [
            {
                "id": r[0], "title": r[1], "location": r[2], "url": r[3],
                "salary_text": r[4],
                "skills": r[5].split(",") if r[5] else [],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_all_postings(db_path: str = DB_PATH) -> List[dict]:
    """All postings across every company currently in the DB — used to
    build the search index for the role/location search bar.
    """
    conn = init_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, company, title, location, url, salary_text, skills FROM postings"
        ).fetchall()
        return [
            {
                "id": r[0], "company": r[1], "title": r[2], "location": r[3],
                "url": r[4], "salary_text": r[5],
                "skills": r[6].split(",") if r[6] else [],
            }
            for r in rows
        ]
    finally:
        conn.close()