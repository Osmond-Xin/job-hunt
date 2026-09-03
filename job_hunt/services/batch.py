"""Run many job evaluations concurrently against a spend cap.

Pulled out of `evaluate-batch`'s command body, which was hiding a real
concurrent job runner: a semaphore-bounded gather over the evaluate graph, a
spend cap driven by a JSONL ledger, and provider-degradation detection. The
command keeps file parsing, clamping, preflight messaging, the Rich table and
the exit code — everything here is "run the jobs and say what happened to
each one".
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from job_hunt.cli._shared import _resolve_source_type
from job_hunt.graphs.evaluate_job import build_evaluate_job_graph
from job_hunt.nodes._llm import LLM_FAILURE_MARKER
from job_hunt.services.usage_ledger import _ledger_line_count, _ledger_spend_since


@dataclass(frozen=True)
class JobOutcome:
    target: str
    company: str = "?"
    role: str = "?"
    score_value: float | None = None
    score: str = "—"
    recommendation: str = "skip"
    report: str = ""
    errors: list[str] = field(default_factory=list)
    artifact_warnings: list[str] = field(default_factory=list)
    failed: str = ""
    skipped_over_budget: bool = False
    degraded: bool = False


@dataclass(frozen=True)
class BatchOutcome:
    jobs: list[JobOutcome]
    spend: float
    unmeasurable: bool
    budget_capped: bool


def run_batch(
    targets: list[str],
    *,
    concurrency: int,
    max_cost: float,
    generate_cover_letter: bool,
) -> BatchOutcome:
    """Evaluate `targets` concurrently, stopping early once `max_cost` is hit.

    Each job runs the same graph as `evaluate`, so jobs that clear the score
    gate still get their CV and cover letter written on the premium tier.
    """
    ledger_start = _ledger_line_count()
    budget_stop = asyncio.Event() if max_cost > 0 else None
    # Set when premium spend is happening but is not being recorded, which
    # is a different failure from simply hitting the cap.
    unmeasurable = asyncio.Event()

    async def run_all() -> list[JobOutcome]:
        graph = build_evaluate_job_graph()
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def run_one(target: str) -> JobOutcome:
            if budget_stop is not None and budget_stop.is_set():
                return JobOutcome(target=target, skipped_over_budget=True)
            async with semaphore:
                if budget_stop is not None and budget_stop.is_set():
                    return JobOutcome(target=target, skipped_over_budget=True)
                run_id = uuid.uuid4().hex
                source_type = _resolve_source_type(target, "auto")
                try:
                    result = await graph.ainvoke(
                        {
                            "run_id": run_id,
                            "thread_id": run_id,
                            "input": target,
                            "source_type": source_type,
                            "url": target if source_type == "url" else None,
                            "generate_cover_letter": generate_cover_letter,
                            "errors": [],
                        },
                        config={"configurable": {"thread_id": run_id}},
                    )
                except Exception as exc:  # one bad JD must not sink the batch
                    return JobOutcome(target=target, failed=f"{type(exc).__name__}: {exc}")
                finally:
                    if budget_stop is not None:
                        spent, premium_records, priced_records = _ledger_spend_since(ledger_start)
                        if premium_records > priced_records:
                            # Any premium call that reported no cost makes the
                            # running total an undercount, so the cap trips late
                            # or never. Partial data is not safer than none —
                            # an unmeasurable budget is not a budget.
                            unmeasurable.set()
                            budget_stop.set()
                        elif spent >= max_cost:
                            budget_stop.set()
                jd_meta = result.get("jd_meta")
                scores = result.get("scores")
                errors = result.get("errors") or []
                return JobOutcome(
                    target=target,
                    company=jd_meta.company if jd_meta else "?",
                    role=jd_meta.title if jd_meta else "?",
                    score_value=scores.weighted_total if scores else None,
                    score=f"{scores.weighted_total:.2f}" if scores else "—",
                    recommendation=result.get("recommendation", "skip"),
                    report=result.get("report_path") or "",
                    errors=errors,
                    artifact_warnings=result.get("artifact_warnings") or [],
                    # A job whose LLM calls all failed still "completes" — every
                    # node falls back to placeholder content and the tracker row
                    # is written as if it were real. A provider outage would
                    # otherwise show up as 50 successes.
                    degraded=any(LLM_FAILURE_MARKER in error for error in errors),
                )

        return await asyncio.gather(*(run_one(target) for target in targets))

    jobs = asyncio.run(run_all())
    spend, _premium_records, _priced_records = _ledger_spend_since(ledger_start)
    return BatchOutcome(
        jobs=list(jobs),
        spend=spend,
        unmeasurable=unmeasurable.is_set(),
        budget_capped=budget_stop is not None and budget_stop.is_set(),
    )
