"""write_tracker_addition and merge_or_update_tracker nodes."""

from __future__ import annotations

import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository

_TRACKER_PATH = Path("data/applications.md")
_MATCH_THRESHOLD = 0.70


async def write_tracker_addition(state: JobHuntState, config: RunnableConfig) -> dict:
    """Append a new row to the tracker if the job does not already exist."""
    repo = TrackerRepository(_TRACKER_PATH)
    jd_meta = state.get("jd_meta")
    scores = state.get("scores")
    report_path = state.get("report_path")
    pdf_path = state.get("pdf_path")
    run_id = state.get("run_id", "")

    company = jd_meta.company if jd_meta else ""
    role = jd_meta.title if jd_meta else ""

    existing, match_score = repo.find_match(company=company, role=role)
    if existing and match_score >= _MATCH_THRESHOLD:
        return {"tracker_entry": existing, "errors": []}

    repo.ensure_exists()
    all_entries = repo.parse()
    next_num = max((e.number for e in all_entries), default=0) + 1

    score_str = f"{scores.weighted_total:.1f}/5" if scores else "N/A"
    status = "Evaluated"
    pdf_flag = "✅" if pdf_path else "❌"
    report_ref = report_path or run_id[:8] or "—"

    entry = TrackerEntry(
        number=next_num,
        date=datetime.date.today().isoformat(),
        company=company,
        role=role,
        score=score_str,
        status=status,
        pdf=pdf_flag,
        report=report_ref,
        notes=state.get("recommendation", ""),
    )
    repo.append_entry(entry)
    return {"tracker_entry": entry, "errors": []}


async def merge_or_update_tracker(state: JobHuntState, config: RunnableConfig) -> dict:
    """If a matching row already exists, update score/status/report fields."""
    repo = TrackerRepository(_TRACKER_PATH)
    jd_meta = state.get("jd_meta")
    scores = state.get("scores")
    report_path = state.get("report_path")
    pdf_path = state.get("pdf_path")

    company = jd_meta.company if jd_meta else ""
    role = jd_meta.title if jd_meta else ""

    existing, match_score = repo.find_match(company=company, role=role)
    if not existing or match_score < _MATCH_THRESHOLD:
        return {"errors": []}

    score_str = f"{scores.weighted_total:.1f}/5" if scores else existing.score
    updated = TrackerEntry(
        number=existing.number,
        date=existing.date,
        company=existing.company,
        role=existing.role,
        score=score_str,
        status=existing.status if existing.status != "Applied" else "Applied",
        pdf="✅" if pdf_path else existing.pdf,
        report=report_path or existing.report,
        notes=existing.notes,
    )
    repo.update_entry(updated)
    return {"tracker_entry": updated, "errors": []}
