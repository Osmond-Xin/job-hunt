"""write_report node — assembles final evaluation report from blocks."""

from __future__ import annotations

import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState

_REPORTS_DIR = Path("reports")


async def write_report(state: JobHuntState, config: RunnableConfig) -> dict:
    jd_meta = state.get("jd_meta")
    scores = state.get("scores")
    blocks = state.get("evaluation_blocks", {})
    run_id = state.get("run_id", "unknown")

    company = jd_meta.company if jd_meta else "Unknown"
    role = jd_meta.title if jd_meta else "Unknown"
    date = datetime.date.today().isoformat()

    recommendation = state.get("recommendation", "skip")
    score_str = f"{scores.weighted_total:.1f}/5.0" if scores else "N/A"
    archetype = state.get("archetype")
    archetype_label = archetype.archetype if archetype else "unknown"
    url = state.get("url") or ""

    lines = [
        f"# Evaluation Report: {company} — {role}",
        f"**Date**: {date}  |  **Score**: {score_str}  |  **Recommendation**: {recommendation.upper()}",
        f"**Archetype**: {archetype_label}",
    ]
    if url:
        lines.append(f"**URL**: {url}")
    lines.append("")

    if scores:
        lines += ["## Score Breakdown", ""]
        for dim in scores.dimensions:
            lines.append(f"- **{dim.dimension}** ({dim.weight*100:.0f}%): {dim.score:.1f}/5 — {dim.rationale}")
        lines += ["", f"**Weighted total**: {scores.weighted_total:.2f}/5.0", ""]
        if scores.recommendation_rationale:
            lines += [f"**Rationale**: {scores.recommendation_rationale}", ""]

    section_titles = {
        "role_summary": "Role Summary",
        "cv_match": "CV Match",
        "level_strategy": "Level & Strategy",
        "comp_research": "Company & Compensation Research",
        "personalization": "Personalization Plan",
        "interview_prep": "Interview Preparation",
        # Letter body included so the operator can review the prose without
        # opening the PDF artifact.
        "cover_letter": "Cover Letter (as generated)",
    }
    for key, title in section_titles.items():
        if blocks.get(key):
            lines += [f"## {title}", "", blocks[key], ""]

    if blocks.get("draft_answers"):
        lines += [blocks["draft_answers"].strip(), ""]

    if scores and scores.pdf_content.keywords:
        keywords_line = ", ".join(f"`{kw}`" for kw in scores.pdf_content.keywords)
        lines += ["## ATS Keywords", "", keywords_line, ""]

    report_md = "\n".join(lines)

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"{date}-{_slug(company)}-{_slug(role)}"
    report_path = _REPORTS_DIR / f"{slug}-{run_id[:8]}.md"
    report_path.write_text(report_md, encoding="utf-8")

    return {"report_md": report_md, "report_path": str(report_path), "errors": []}


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30]
