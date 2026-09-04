"""write_tracker_addition and merge_or_update_tracker nodes."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from job_hunt.models.state import JobHuntState
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository
from job_hunt.services.employer_match import EmployerMatcher, is_reliable_match, load_aliases
from job_hunt.services.llm.call import LLM_FAILURE_MARKER

_TRACKER_PATH = Path("data/applications.md")

# score_and_recommend's own fallback tag (see services/llm/call.py's
# call_node_llm_or_fallback, which prefixes every failure message with
# "{node_name} {LLM_FAILURE_MARKER}"). A different node falling back —
# interview_prep, comp_research, ... — still leaves scores real; only this
# node's failure means the number in state["scores"] is the placeholder
# "0.0/5, skip" from evaluate.py's fallback_content, not an assessment.
_SCORE_FAILURE_TAG = f"score_and_recommend {LLM_FAILURE_MARKER}"

# Written into the tracker row's notes, and mirrored by report.py's header,
# when scoring itself never happened. A run whose scoring fell back must not
# leave a row that reads like a completed, low-score evaluation — the row
# that inspired this: #867, "0.0/5 | Evaluated | ... | skip", after a
# provider outage returned 529 on every call.
NEEDS_RERUN_NOTE = (
    "NEEDS RE-RUN — scoring failed (LLM provider unavailable); this job was not assessed."
)


def scoring_failed(errors: list[str] | None) -> bool:
    """True when this run's ``score_and_recommend`` call fell back.

    Deliberately narrower than ``services/batch.py``'s ``degraded`` flag,
    which fires on *any* node's LLM failure and only ever drives a console
    warning. Here the result decides what gets written as fact into
    ``data/applications.md`` (and, via ``report.py``, the report header), so
    it must fire only for the one node whose fallback actually makes the
    score meaningless.
    """
    return any(_SCORE_FAILURE_TAG in error for error in errors or [])


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


def _unattended_match(matcher: EmployerMatcher, *, company: str, role: str) -> TrackerEntry | None:
    """The gate for this module's own writes — evaluate/evaluate-batch, with
    company/role LLM-extracted from a scraped JD and nobody confirming them.

    ``EmployerMatcher.best(intent="mutate")`` is find_match's original 0.70
    threshold, calibrated for a caller a human is actually driving
    (``cli/apply.py``'s ``--company``/``--role``/``--confirmed``). Nobody is
    in the loop here, so this mirrors ``email/reconcile.py``'s own pattern
    instead: a raw match, gated by ``is_reliable_match`` before it is safe to
    act on.
    """
    entry, score = matcher.raw_match(company=company, role=role)
    if entry is None:
        return None
    if not is_reliable_match(
        company=company, role=role,
        matched_company=entry.company, matched_role=entry.role,
        score=score,
    ):
        return None
    return entry


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
    score_failed = scoring_failed(state.get("errors"))

    company = jd_meta.company if jd_meta else ""
    role = jd_meta.title if jd_meta else ""
    if not company and not role:
        # extract_jd already ran every identity-recovery trick it has
        # (_strip_board_suffix, _identity_from_pipeline) before jd_meta got
        # here — there is nothing left to try. A blank/blank row is what
        # `tracker verify`'s (company, role) dedupe key treats as identical to
        # every other blank/blank row, so most of its "possible duplicates"
        # warnings are really this. The URL is still unique per job and
        # idempotent across retries of the same broken extraction, so use it
        # rather than leaving both fields empty.
        company = "Unknown"
        role = (state.get("url") or "").strip() or (f"run {run_id[:8]}" if run_id else "unidentified")

    matcher = EmployerMatcher(repo.parse(), aliases=load_aliases())
    existing = _unattended_match(matcher, company=company, role=role)
    if existing:
        _mark_pipeline_processed(state, existing)
        return {"tracker_entry": existing, "errors": []}

    repo.ensure_exists()
    all_entries = repo.parse()
    next_num = max((e.number for e in all_entries), default=0) + 1

    if score_failed:
        # Do not assert a score that was never computed. A later re-run over
        # the same company/role will match this row via _unattended_match
        # above and merge_or_update_tracker will fill in the real score
        # without creating a duplicate.
        score_str = "N/A"
        notes = NEEDS_RERUN_NOTE
    else:
        score_str = f"{scores.weighted_total:.1f}/5" if scores else "N/A"
        notes = state.get("recommendation", "")
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
        notes=notes,
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
    score_failed = scoring_failed(state.get("errors"))

    company = jd_meta.company if jd_meta else ""
    role = jd_meta.title if jd_meta else ""

    matcher = EmployerMatcher(repo.parse(), aliases=load_aliases())
    existing = _unattended_match(matcher, company=company, role=role)
    if not existing:
        return {"errors": []}

    # A scoring failure on a re-run must not clobber a real score a previous
    # run already wrote for this row — treat it the same as "no scores at
    # all" and keep what's there.
    score_str = existing.score if score_failed else (
        f"{scores.weighted_total:.1f}/5" if scores else existing.score
    )
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
