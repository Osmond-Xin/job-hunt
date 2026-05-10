"""HITL interrupt node used by apply_assistant graph."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from job_hunt.models.state import JobHuntState


async def stop_before_submit(state: JobHuntState, config: RunnableConfig) -> dict:
    """Pause the graph and surface the filled-form summary to the user.

    The graph resumes only when the user calls graph.invoke(..., Command(resume=decision))
    with decision = {"submitted": True} or {"submitted": False}.
    """
    jd_meta = state.get("jd_meta")
    scores = state.get("scores")
    report_path = state.get("report_path")

    summary = (
        f"Ready to submit application for {jd_meta.company if jd_meta else 'Unknown'} "
        f"— {jd_meta.title if jd_meta else 'Unknown'}\n"
        f"Score: {scores.weighted_total:.1f}/5.0\n"
        f"Report: {report_path or 'not generated'}\n\n"
        "Please review and submit manually in the browser, then confirm below."
    )

    decision = interrupt({"summary": summary})
    return {"human_decision": decision, "errors": []}


async def update_tracker_after_submit(state: JobHuntState, config: RunnableConfig) -> dict:
    """Run after human confirms submission; update tracker status to Applied."""
    from job_hunt.repositories.tracker_repo import TrackerRepository

    decision = state.get("human_decision", {})
    if not decision.get("submitted"):
        return {"errors": []}

    tracker_entry = state.get("tracker_entry")
    if not tracker_entry:
        return {"errors": ["No tracker entry to update after submission."]}

    from pathlib import Path
    repo = TrackerRepository(Path("data/applications.md"))

    from job_hunt.repositories.tracker_repo import TrackerEntry
    updated = TrackerEntry(
        number=tracker_entry.number,
        date=tracker_entry.date,
        company=tracker_entry.company,
        role=tracker_entry.role,
        score=tracker_entry.score,
        status="Applied",
        pdf=tracker_entry.pdf,
        report=tracker_entry.report,
        notes=tracker_entry.notes,
    )
    repo.update_entry(updated)
    return {"tracker_entry": updated, "errors": []}
