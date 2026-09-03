"""write_tracker_addition and merge_or_update_tracker nodes."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository
from job_hunt.services.employer_match import EmployerMatcher, load_aliases

_TRACKER_PATH = Path("data/applications.md")

# Both nodes read-modify-write the whole tracker file and derive the next row
# number from what they read. TrackerRepository is fully synchronous, so the
# critical sections below cannot currently be interleaved by asyncio and this
# lock is insurance rather than a fix — it holds the invariant if anyone later
# introduces an `await` between the read and the write.
#
# What it does NOT protect against: a second OS process. Two `evaluate-batch`
# runs, or a batch racing the scheduler's email ingest, can still duplicate row
# numbers. Do not run them at the same time.
_WRITE_LOCK = asyncio.Lock()


async def write_tracker_addition(state: JobHuntState, config: RunnableConfig) -> dict:
    """Append a new row to the tracker if the job does not already exist."""
    async with _WRITE_LOCK:
        return await _write_tracker_addition(state)


async def _write_tracker_addition(state: JobHuntState) -> dict:
    repo = TrackerRepository(_TRACKER_PATH)
    jd_meta = state.get("jd_meta")
    scores = state.get("scores")
    report_path = state.get("report_path")
    pdf_path = state.get("pdf_path")
    run_id = state.get("run_id", "")

    company = jd_meta.company if jd_meta else ""
    role = jd_meta.title if jd_meta else ""

    matcher = EmployerMatcher(repo.parse(), aliases=load_aliases())
    match = matcher.best(company=company, role=role, intent="mutate")
    if match:
        existing = match.entry
        _mark_pipeline_processed(state, existing)
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
    _mark_pipeline_processed(state, entry)
    return {"tracker_entry": entry, "errors": []}


def _mark_pipeline_processed(state: JobHuntState, entry: TrackerEntry) -> None:
    """Check this job's row off in pipeline.md, by URL.

    Only `job-hunt pipeline run` used to do this, and nobody runs that — the
    real paths are `evaluate` and `evaluate-batch`, which left every row
    pending forever. 3,365 of them had accumulated, so triage kept re-ranking
    and re-screening jobs that were evaluated weeks ago, and an already-scored
    posting could come back to the top of the shortlist and be paid for twice.

    The tracker's own company+role dedupe cannot cover this: discovery records
    whatever name its source used ("Cscgeneration 2" from one board, "CSC
    Generation" from another), and no fuzzy key survives that. The URL does.

    Best-effort by design: a URL that is not pending — a hand-typed target, a
    JD file, a row already ticked off — simply finds nothing. This runs inside
    the node's write lock, so a batch's concurrent jobs cannot interleave the
    read-modify-write of the file.
    """
    url = (state.get("url") or "").strip()
    if not url:
        return
    try:
        from job_hunt.services import pipeline_inbox

        pipeline_inbox.mark_evaluated(url, tracker_id=entry.number, score=entry.score)
    except Exception:  # noqa: BLE001 — bookkeeping must never sink a paid run
        pass


async def merge_or_update_tracker(state: JobHuntState, config: RunnableConfig) -> dict:
    """If a matching row already exists, update score/status/report fields."""
    async with _WRITE_LOCK:
        return await _merge_or_update_tracker(state)


async def _merge_or_update_tracker(state: JobHuntState) -> dict:
    repo = TrackerRepository(_TRACKER_PATH)
    jd_meta = state.get("jd_meta")
    scores = state.get("scores")
    report_path = state.get("report_path")
    pdf_path = state.get("pdf_path")

    company = jd_meta.company if jd_meta else ""
    role = jd_meta.title if jd_meta else ""

    matcher = EmployerMatcher(repo.parse(), aliases=load_aliases())
    match = matcher.best(company=company, role=role, intent="mutate")
    if not match:
        return {"errors": []}
    existing = match.entry

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
