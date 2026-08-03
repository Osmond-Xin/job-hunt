"""write_report node — assembles final evaluation report from blocks."""

from __future__ import annotations

import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.nodes.artifact_paths import run_stem

_REPORTS_DIR = Path("reports")


async def write_report(state: JobHuntState, config: RunnableConfig) -> dict:
    jd_meta = state.get("jd_meta")
    scores = state.get("scores")
    blocks = state.get("evaluation_blocks", {})

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

    # Right under the header, before anything else: console output scrolls away
    # during a long batch, the report is what the operator still has when they
    # come back to decide whether to send this.
    artifact_warnings = state.get("artifact_warnings") or []
    if artifact_warnings:
        lines += ["## ⚠️ Artifacts needing review", ""]
        lines += [f"- {warning}" for warning in artifact_warnings]
        lines += ["", "Do not send these without reading them first.", ""]

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
    report_path = _REPORTS_DIR / f"{run_stem(state)}.md"
    report_path.write_text(report_md, encoding="utf-8")

    return {"report_md": report_md, "report_path": str(report_path), "errors": []}
