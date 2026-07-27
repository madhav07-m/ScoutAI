"""
Phase 2 — Chunking strategy.

Decision: SECTION-LEVEL chunking, not whole-document embedding.

Why: whole-document embedding gives one similarity number and no way to
explain it. Section-level chunking lets us say "this resume matched
strongly on Experience but had a weak Skills section" — which is
exactly the kind of explainability a gap-analysis feature needs later
(Phase 6). It costs more embedding calls, but they're cheap and local
(MiniLM), so the trade-off is easy.

Section detection is heading-based: we look for common resume section
headers (Experience, Education, Skills, Projects, Summary, etc.) and
split on them. Anything before the first recognized heading is treated
as a "Summary/Header" chunk. If no headings are found at all (some
resumes are unstructured), the whole document becomes a single
"General" chunk — so the pipeline degrades gracefully instead of
crashing.
"""

import re
from typing import Dict, List

SECTION_HEADERS = [
    "summary", "objective", "profile",
    "experience", "work experience", "professional experience", "employment",
    "education", "academic background",
    "skills", "technical skills", "core competencies",
    "projects", "personal projects",
    "certifications", "certificates",
    "achievements", "awards",
    "publications",
    "extracurricular", "activities",
]

# Build a regex that matches a line which is JUST a header (short line,
# case-insensitive, optionally followed by a colon).
_HEADER_PATTERN = re.compile(
    r"^\s*(" + "|".join(re.escape(h) for h in SECTION_HEADERS) + r")\s*:?\s*$",
    re.IGNORECASE,
)


def chunk_by_section(text: str) -> Dict[str, str]:
    """Split cleaned resume/JD text into named section chunks.

    Returns a dict of {section_name: section_text}. Falls back to a
    single {"General": text} chunk if no headings are detected.
    """
    lines = text.split("\n")
    sections: Dict[str, List[str]] = {}
    current_section = None

    for line in lines:
        stripped = line.strip()
        match = _HEADER_PATTERN.match(stripped)
        if match:
            current_section = match.group(1).title()
            sections.setdefault(current_section, [])
            continue

        if current_section is None:
            sections.setdefault("Summary/Header", [])
            sections["Summary/Header"].append(stripped)
        else:
            sections[current_section].append(stripped)

    # Join lines back, drop empty sections
    result = {
        name: "\n".join(l for l in content if l).strip()
        for name, content in sections.items()
    }
    result = {k: v for k, v in result.items() if v}

    if not result:
        result = {"General": text.strip()}

    return result


def chunks_to_list(name: str, section_dict: Dict[str, str]) -> List[dict]:
    """Flatten a {section: text} dict into a list of chunk records
    tagged with a source document name, ready for embedding/storage.
    """
    return [
        {"doc_name": name, "section": section, "text": text}
        for section, text in section_dict.items()
    ]
