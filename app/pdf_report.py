"""
Generates a clean, downloadable PDF version of a single resume's gap
analysis report — the same content shown in the app (fit scores,
strengths, gaps, suggestions), formatted for sharing outside the app.
"""

import io
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)


def _bullet_list(items, style):
    """Render a list of strings as '- item' paragraphs (avoids ReportLab
    bullet-list quirks, keeps things simple and reliably rendered)."""
    return [Paragraph(f"– {item}", style) for item in items]


def build_gap_analysis_pdf(
    resume_name: str,
    fit_score: float,
    llm_report: dict,
) -> bytes:
    """Build a one-resume gap analysis PDF and return its bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleStyle", parent=styles["Title"], fontSize=18, spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        "SubtitleStyle", parent=styles["Normal"], fontSize=10,
        textColor=colors.HexColor("#555555"), spaceAfter=16,
    )
    heading_style = ParagraphStyle(
        "HeadingStyle", parent=styles["Heading2"], fontSize=13,
        spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1a1a1a"),
    )
    body_style = ParagraphStyle(
        "BodyStyle", parent=styles["Normal"], fontSize=10.5, leading=15,
    )

    story = []
    story.append(Paragraph("Resume Gap Analysis Report", title_style))
    story.append(Paragraph(f"Resume: {resume_name}", subtitle_style))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#dddddd")))
    story.append(Spacer(1, 12))

    # Score summary table
    llm_score = llm_report.get("fit_score")
    score_data = [
        ["Embedding Fit Score", f"{fit_score}/100"],
        ["LLM-Assessed Score", f"{llm_score}/100" if llm_score is not None else "N/A"],
    ]
    score_table = Table(score_data, colWidths=[2.5 * inch, 2 * inch])
    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f5")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#333333")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Note: these two scores measure different things and can "
        "legitimately disagree — the embedding score reflects vector "
        "similarity to the job description, while the LLM score reflects "
        "Gemini's qualitative judgment of the same matched content.",
        ParagraphStyle("NoteStyle", parent=body_style, fontSize=9,
                        textColor=colors.HexColor("#777777"), leading=13),
    ))

    strengths = llm_report.get("strengths", [])
    if strengths:
        story.append(Paragraph("Strengths", heading_style))
        story.extend(_bullet_list(strengths, body_style))

    gaps = llm_report.get("gaps", [])
    if gaps:
        story.append(Paragraph("Gaps", heading_style))
        story.extend(_bullet_list(gaps, body_style))

    suggestions = llm_report.get("suggestions", [])
    if suggestions:
        story.append(Paragraph("Suggestions", heading_style))
        story.extend(_bullet_list(suggestions, body_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
