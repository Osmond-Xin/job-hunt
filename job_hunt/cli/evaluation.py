from __future__ import annotations

import shutil
import asyncio
import os
import re
import uuid
from pathlib import Path
import typer
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
from job_hunt.graphs.evaluate_job import build_evaluate_job_graph
from job_hunt.services.llm.call import LLM_FAILURE_MARKER
from job_hunt.services.batch import run_batch
from job_hunt.services.llm.local_command import wants_json as provider_wants_json
from job_hunt.services.usage_ledger import (
    _ledger_cost_since,
    _ledger_line_count,
    _ledger_path,
    _ledger_spend_since,
)
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services import cv_sync_check
from job_hunt.services.employer_match import ALREADY_EVALUATED_THRESHOLD, EmployerMatcher

from ._render import _short, console
from job_hunt.services.profile_loader import _apply_profile_values
from job_hunt.services.web_extract import _extract_loop_url_metadata
from job_hunt.services.source_type import _resolve_source_type
from . import app


@app.command("evaluate")
def evaluate(
    target: str,
    source_type: str = typer.Option("auto", help="auto, url, jd_text, or local_file."),
    trace: bool | None = typer.Option(None, help="Temporarily enable/disable LangSmith for this run."),
    cover_letter: bool | None = typer.Option(
        None,
        "--cover-letter/--no-cover-letter",
        help="Generate a standalone one-page cover letter PDF. Defaults to apply.cover_letter_default in profile.yml.",
    ),
) -> None:
    if trace is not None:
        os.environ["JOB_HUNT_LANGSMITH_ENABLED"] = "true" if trace else "false"

    if os.environ.get("JOB_HUNT_SKIP_CV_SYNC_CHECK") != "1":
        sync_result = cv_sync_check.run()
        for warning in sync_result.warnings:
            console.print(f"[yellow]cv-sync-check:[/yellow] {warning}")
        if sync_result.errors:
            for error in sync_result.errors:
                console.print(f"[red]cv-sync-check:[/red] {error}")
            console.print("[red]Aborting evaluate.[/red] Run `job-hunt tracker check-sync` for details.")
            raise typer.Exit(1)

    resolved_source = _resolve_source_type(target, source_type)
    run_id = uuid.uuid4().hex
    profile_values = _apply_profile_values()
    if cover_letter is None:
        generate_cover_letter_flag = bool(profile_values.get("apply_cover_letter_default"))
    else:
        generate_cover_letter_flag = cover_letter
    state = {
        "run_id": run_id,
        "thread_id": run_id,
        "input": target,
        "source_type": resolved_source,
        "url": target if resolved_source == "url" else None,
        "generate_cover_letter": generate_cover_letter_flag,
        "errors": [],
    }

    async def run() -> None:
        graph = build_evaluate_job_graph()
        result = await graph.ainvoke(
            state,
            config={"configurable": {"thread_id": run_id}},
        )
        scores = result.get("scores")
        console.print(f"Run: {run_id}")
        console.print(f"Recommendation: {result.get('recommendation', 'skip')}")
        if scores:
            console.print(f"Score: {scores.weighted_total:.2f}/5.0")
        if result.get("report_path"):
            console.print(f"Report: {result['report_path']}")
        if result.get("pdf_path"):
            console.print(f"PDF: {result['pdf_path']}")
        if result.get("cover_letter_path"):
            console.print(f"Cover letter: {result['cover_letter_path']}")
        for warning in result.get("artifact_warnings") or []:
            console.print(f"[red]ARTIFACT:[/red] {warning}")
        errors = result.get("errors") or []
        for error in errors:
            console.print(f"[yellow]warning:[/yellow] {error}")

    asyncio.run(run())


# Each concurrent job can hold an LLM subprocess open for minutes; more
# parallelism buys latency but risks resource exhaustion on a laptop.
_BATCH_MAX_CONCURRENCY = 8


@app.command("evaluate-batch")
def evaluate_batch(
    urls_file: Path = typer.Argument(..., help="Text file with one job URL (or JD path) per line; '#' comments allowed."),
    concurrency: int = typer.Option(3, "--concurrency", help=f"Jobs evaluated in parallel (max {_BATCH_MAX_CONCURRENCY}). One job is ~10 minutes of mostly-waiting LLM calls."),
    max_jobs: int = typer.Option(60, "--max-jobs", help="Refuse to start if the file holds more targets than this."),
    max_cost: float = typer.Option(0.0, "--max-cost", help="Stop launching new jobs once premium spend for this run exceeds N USD. 0 disables the cap."),
    max_failures: int = typer.Option(0, "--max-failures", help="Exit non-zero when more than this many jobs fail. 0 means any failure is reported as failure."),
    skip_evaluated: bool = typer.Option(True, "--skip-evaluated/--force", help="Skip targets whose company+role already has a tracker row. --force re-evaluates and re-spends."),
    cover_letter: bool | None = typer.Option(
        None,
        "--cover-letter/--no-cover-letter",
        help="Generate cover letter PDFs. Defaults to apply.cover_letter_default in profile.yml.",
    ),
) -> None:
    """Evaluate many jobs in one run and print a single summary table.

    Each job runs the same graph as `evaluate`, so jobs that clear the score
    gate still get their CV and cover letter written on the premium tier. A
    batch is a multi-hour, real-money operation: it preflights once up front
    and stops early rather than burning the whole list on a broken setup.
    """
    targets = [
        line.strip()
        for line in urls_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    # Same target listed twice costs twice; the second result would also just
    # overwrite the first in the tracker.
    targets = list(dict.fromkeys(targets))
    if not targets:
        console.print(f"[red]No targets found in {urls_file}.[/red]")
        raise typer.Exit(1)
    if len(targets) > max_jobs:
        console.print(
            f"[red]{len(targets)} targets exceeds --max-jobs {max_jobs}.[/red] "
            "Split the file or raise the cap deliberately."
        )
        raise typer.Exit(1)
    if concurrency > _BATCH_MAX_CONCURRENCY:
        console.print(
            f"[yellow]Clamping --concurrency {concurrency} to {_BATCH_MAX_CONCURRENCY}[/yellow] "
            "(each job can hold an LLM subprocess open)."
        )
        concurrency = _BATCH_MAX_CONCURRENCY

    if max_cost < 0:
        console.print("[red]--max-cost cannot be negative.[/red]")
        raise typer.Exit(1)

    _batch_preflight(budget_enforced=max_cost > 0)

    profile_values = _apply_profile_values()
    if cover_letter is None:
        generate_cover_letter_flag = bool(profile_values.get("apply_cover_letter_default"))
    else:
        generate_cover_letter_flag = cover_letter

    if skip_evaluated:
        targets, skipped = _partition_already_evaluated(targets)
        for target, entry in skipped:
            console.print(f"[dim]skip (tracker #{entry.number} {entry.status}): {target}[/dim]")
        if not targets:
            console.print("Every target already has a tracker row. Nothing to do (use --force to re-run).")
            return

    console.print(f"Evaluating {len(targets)} jobs, {concurrency} at a time.")
    outcome = run_batch(
        targets,
        concurrency=concurrency,
        max_cost=max_cost,
        generate_cover_letter=generate_cover_letter_flag,
        graph_builder=build_evaluate_job_graph,
    )
    rows = outcome.jobs

    table = Table(title=f"Batch evaluation ({len(rows)} jobs)")
    table.add_column("Company", overflow="fold")
    table.add_column("Role", overflow="fold")
    table.add_column("Score", justify="right")
    table.add_column("Rec")
    table.add_column("Report", overflow="fold")
    # Sort on the numeric score. Sorting the display string put "—" above 5.00.
    for row in sorted(rows, key=lambda item: item.score_value if item.score_value is not None else -1.0, reverse=True):
        if row.failed:
            table.add_row(_short(row.target, 40), "[red]FAILED[/red]", "—", "—", row.failed[:60])
            continue
        if row.skipped_over_budget:
            table.add_row(_short(row.target, 40), "[yellow]OVER BUDGET[/yellow]", "—", "—", "not started")
            continue
        table.add_row(
            _short(row.company, 24),
            _short(row.role, 32),
            row.score,
            row.recommendation,
            _short(row.report, 48),
        )
    console.print(table)

    flagged = [row for row in rows if row.artifact_warnings]
    if flagged:
        console.print(f"\n[red]{len(flagged)} job(s) produced artifacts you must review before sending:[/red]")
        for row in flagged:
            for warning in row.artifact_warnings:
                console.print(f"- {row.company}: {warning}")

    warned = [row for row in rows if row.errors]
    if warned:
        console.print(f"\n[yellow]{len(warned)} job(s) completed with warnings:[/yellow]")
        for row in warned:
            console.print(f"- {row.company}: {row.errors[0]}")

    console.print(f"\nPremium spend this batch: [bold]${outcome.spend:.2f}[/bold] over {len(rows)} job(s).")
    if outcome.unmeasurable:
        console.print(
            "[red]Premium calls recorded no cost, so --max-cost could not be enforced.[/red] "
            "Remaining jobs were not started. Check that the premium command still passes "
            "`--output-format json`."
        )
    elif outcome.budget_capped:
        console.print(f"[yellow]Budget cap ${max_cost:.2f} reached — remaining jobs were not started.[/yellow]")

    # A job whose LLM calls all failed still "completes" — every node falls back
    # to placeholder content and the tracker row is written as if it were real.
    # A provider outage would otherwise show up as 50 successes.
    degraded = [row for row in rows if row.degraded]
    if degraded:
        console.print(
            f"\n[red]{len(degraded)} job(s) ran on fallback content because an LLM "
            "provider failed — their reports and artifacts are not trustworthy:[/red]"
        )
        for row in degraded:
            console.print(f"- {row.company}")

    failures = [row for row in rows if row.failed]
    if len(failures) + len(degraded) > max_failures:
        console.print(
            f"[red]{len(failures)} failed + {len(degraded)} degraded "
            f"(--max-failures {max_failures}).[/red]"
        )
        raise typer.Exit(1)


def _batch_preflight(budget_enforced: bool = False) -> None:
    """Fail before a multi-hour run, not during it.

    A stale CV or a premium command that is not on PATH degrades every job in
    the batch identically, and the damage is only visible hours later.
    """
    if os.environ.get("JOB_HUNT_SKIP_CV_SYNC_CHECK") != "1":
        sync_result = cv_sync_check.run()
        for warning in sync_result.warnings:
            console.print(f"[yellow]cv-sync-check:[/yellow] {warning}")
        if sync_result.errors:
            for error in sync_result.errors:
                console.print(f"[red]cv-sync-check:[/red] {error}")
            console.print("[red]Aborting batch.[/red] Run `job-hunt tracker check-sync` for details.")
            raise typer.Exit(1)

    settings = load_settings()
    command = settings.llm.premium.command
    if not command:
        console.print("[red]No premium command configured (llm.premium.command is empty).[/red]")
        raise typer.Exit(1)
    if not shutil.which(command[0]):
        console.print(
            f"[red]Premium command '{command[0]}' is not on PATH.[/red] Every CV and cover "
            "letter in this batch would silently fall back to the cheap tier."
        )
        raise typer.Exit(1)

    if budget_enforced:
        # --max-cost is computed from ledger records. Without the ledger, or
        # without JSON output to populate cost_usd, the cap reads $0.00 forever
        # and silently lets the whole list run.
        if not settings.observability.local_ledger.enabled:
            console.print(
                "[red]--max-cost needs the local ledger.[/red] Enable "
                "observability.local_ledger or drop the cap."
            )
            raise typer.Exit(1)
        # Ask the provider, not a substring: `["claude", "-p", "json"]` would
        # pass a naive check while recording no cost, and
        # `--output-format=json` would be rejected despite being supported.
        if not provider_wants_json(command):
            console.print(
                "[red]--max-cost needs real cost data.[/red] The premium command must pass "
                "`--output-format json`; otherwise usage is estimated and cost_usd is null."
            )
            raise typer.Exit(1)


def _partition_already_evaluated(targets: list[str]) -> tuple[list[str], list[tuple[str, object]]]:
    """Split targets into (to run, already in the tracker).

    Identity comes from the page itself — the company and title the posting
    states — and nothing else. This used to call `_infer_loop_target`, which
    falls back to fuzzy-matching the whole JD text against every tracker row
    when a page names no company. That is right for `loop`, where the operator
    has already decided which application he is resuming, and wrong here: on
    2026-08-17 an aggregator page for SIGA's Applications Systems Analyst
    matched Cohere's "Software Engineer, Search Applications" at 1.0 and the
    job was silently dropped from the batch as already evaluated.

    A miss now costs a duplicate evaluation, which is the direction to fail in
    — and the pipeline row this target came from is ticked off after a run, so
    a job the tracker cannot recognise still stops coming back.
    """
    tracker = TrackerRepository(Path("data/applications.md"))
    runnable: list[str] = []
    skipped: list[tuple[str, object]] = []
    for target in targets:
        entry = None
        try:
            metadata = _extract_loop_url_metadata(target)
            company = (metadata.get("company") or "").strip()
            role = _strip_aggregator_suffix(metadata.get("title") or "")
            if company and role:
                entry, score = EmployerMatcher(tracker.parse()).raw_match(company=company, role=role)
                if score < ALREADY_EVALUATED_THRESHOLD:
                    entry = None
        except Exception:
            entry = None
        if entry is not None:
            skipped.append((target, entry))
        else:
            runnable.append(target)
    return runnable, skipped


def _strip_aggregator_suffix(title: str) -> str:
    """Drop the board's own name from a page title.

    Aggregators append their brand: "AI Solutions Engineer - adzuna.ca". Left
    on, the role never matches the tracker's "AI Solutions Engineer" and the
    same posting gets paid for twice.
    """
    return re.sub(r"\s*[-|–]\s*(?:www\.)?[\w.-]+\.(?:ca|com|org|net)\s*$", "", title).strip()
