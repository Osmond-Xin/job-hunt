from __future__ import annotations

import asyncio
import typer
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.review_repo import ReviewRepository
from job_hunt.services.activity import ActivityEvent, ActivityLogger, read_activity
from job_hunt.services.email.message_parser import ParsedEmail, classify_email_event
from job_hunt.services.email.poller import poll_gmail
from job_hunt.services.email.reconcile import reconcile_email_events
from job_hunt.services.email.review import approve_email_event, ignore_email_event, list_review_candidates

from ._render import _short, console
from . import email_app, review_app


@email_app.command("poll")
def email_poll(
    since: str = "30d",
    max_results: int = 25,
    live: bool = False,
    include_unknown: bool = False,
) -> None:
    settings = load_settings()
    console.print(f"Gmail polling for since={since}.")
    console.print(f"Query: {settings.email_ingest.query.strip()[:160]}...")
    console.print(f"Token path: {settings.email_ingest.token_path}")
    if not live:
        console.print("Dry-run only. Pass --live to call Gmail API.")
        return
    _warn_malformed_events(EmailEventRepository())
    result = poll_gmail(settings, max_results=max_results, include_unknown=include_unknown)
    ActivityLogger(settings.activity).emit(
        ActivityEvent(
            type="email.poll_completed",
            level="info",
            summary=f"Gmail poll completed: {result.events_created} events from {result.scanned} messages",
            payload={
                "scanned": result.scanned,
                "skipped_seen": result.skipped_seen,
                "events_created": result.events_created,
            },
        )
    )
    console.print(f"Scanned: {result.scanned}")
    console.print(f"Skipped seen: {result.skipped_seen}")
    console.print(f"Events created: {result.events_created}")
    for event in result.events:
        console.print(f"- {event.event_type}: {event.company or '?'} / {event.role or '?'} ({event.confidence:.2f})")


@email_app.command("summarize")
def email_summarize(
    since: str = "120d",
    limit: int = 0,
    live: bool = False,
    concurrency: int = 3,
) -> None:
    """LLM-scan the mailbox: classify + summarize every email into data/email-summaries.jsonl."""
    from job_hunt.services.email.summarize import SUMMARY_PATH, summarize_mailbox

    settings = load_settings()
    console.print(f"Mailbox summarize since={since} (broad query; LLM does the filtering).")
    console.print(f"Output: {SUMMARY_PATH}")
    if not live:
        console.print("Dry-run only. Pass --live to call Gmail + MiniMax.")
        return
    result = asyncio.run(
        summarize_mailbox(
            settings,
            since=since,
            limit=limit,
            concurrency=concurrency,
            progress=lambda msg: console.print(f"[dim]{msg}[/dim]"),
        )
    )
    ActivityLogger(settings.activity).emit(
        ActivityEvent(
            type="email.summarize_completed",
            level="info",
            summary=(
                f"Mailbox summarize: {result.summarized} summarized, "
                f"{result.skipped_done} already done, {result.errors} errors"
            ),
            payload={
                "listed": result.listed,
                "skipped_done": result.skipped_done,
                "summarized": result.summarized,
                "errors": result.errors,
            },
        )
    )
    console.print(f"Listed: {result.listed}")
    console.print(f"Already summarized: {result.skipped_done}")
    console.print(f"Summarized now: {result.summarized}")
    console.print(f"Errors: {result.errors}")
    if result.error_ids:
        console.print(f"[yellow]Retry errors by re-running the same command (resumable).[/yellow]")


def _warn_malformed_events(repo: EmailEventRepository) -> None:
    """Say out loud that rows were skipped, so a silent gap can't build up."""
    malformed = repo.malformed()
    if malformed:
        console.print(
            f"[yellow]warning: {len(malformed)} unreadable row(s) in {repo.path} were skipped. "
            f"Run `job-hunt email verify` to see them.[/yellow]"
        )


@email_app.command("verify")
def email_verify() -> None:
    """Check data/email-events.jsonl is readable end to end.

    One row outside the schema used to raise a pydantic error out of every
    command that touches the event log (poll, events, reconcile, review).
    The reader now skips such rows; this command is how you find them.
    """
    repo = EmailEventRepository()
    events = repo.list(limit=100000)
    malformed = repo.malformed()
    console.print(f"Readable events: {len(events)}")
    if not malformed:
        console.print("Event log verification passed.")
        return
    console.print(f"[red]Unreadable rows: {len(malformed)}[/red]")
    for bad in malformed:
        console.print(f"[red]  line {bad.line_number}:[/red] {bad.reason}")
        console.print(f"    {bad.raw[:200]}")
    console.print(
        "\nFix each line by hand, then re-run. Valid values are listed in "
        "job_hunt/models/events.py (source, event_type)."
    )
    raise typer.Exit(1)


@email_app.command("gaps")
def email_gaps(since: str = typer.Option("2026-08-01", help="Only look at mail on or after this date (YYYY-MM-DD).")) -> None:
    """Applications the mailbox knows about that the tracker does not.

    Reads the LLM summaries written by `email summarize` and compares them with
    data/applications.md. Read-only on purpose: an email body is untrusted
    input, so recording is the operator's call. Run `email summarize --live`
    first so the summaries are current.
    """
    from job_hunt.services.email.gaps import find_gaps

    gaps = find_gaps(since=since)
    if not gaps:
        console.print(f"No tracker gaps found in mail since {since}.")
        return

    untracked = [gap for gap in gaps if gap.kind == "untracked"]
    stale = [gap for gap in gaps if gap.kind == "stale_status"]
    advances = [gap for gap in gaps if gap.kind == "advance"]

    if advances:
        console.print(f"\n[bold]{len(advances)} row(s) the mail says moved forward:[/bold]")
        table = Table("Date", "Row", "Status", "Company", "Role", "Subject")
        for gap in advances:
            table.add_row(
                gap.date,
                f"#{gap.entry.number}",
                gap.entry.status,
                _short(gap.company, 22),
                _short(gap.role, 30),
                _short(gap.subject, 34),
            )
        console.print(table)
        console.print("An interview invitation is the one signal worth acting on today.")

    if untracked:
        console.print(f"\n[yellow]{len(untracked)} application(s) with mail but no tracker row:[/yellow]")
        table = Table("Date", "Category", "Company", "Role")
        for gap in untracked:
            table.add_row(gap.date, gap.category, _short(gap.company, 30), _short(gap.role, 46))
        console.print(table)
        console.print("Record one with: job-hunt apply '<url>' --company '...' --role '...' --no-browser --confirmed")

    if stale:
        console.print(f"\n[yellow]{len(stale)} tracker row(s) whose mail says the application is closed:[/yellow]")
        table = Table("Date", "Row", "Status", "Company", "Role")
        for gap in stale:
            table.add_row(
                gap.date,
                f"#{gap.entry.number}",
                gap.entry.status,
                _short(gap.company, 26),
                _short(gap.role, 40),
            )
        console.print(table)


@email_app.command("events")
def email_events(limit: int = 20, needs_review: bool = False) -> None:
    repo = EmailEventRepository()
    _warn_malformed_events(repo)
    events = repo.list(limit=limit, needs_review=needs_review)
    table = Table("Time", "Type", "Company", "Role", "Confidence", "Review", "Subject")
    for event in events:
        table.add_row(
            event.event_time.isoformat(),
            event.event_type,
            event.company or "",
            event.role or "",
            f"{event.confidence:.2f}",
            "yes" if event.needs_review else "no",
            event.subject[:60],
        )
    console.print(table)


@email_app.command("parse-sample")
def email_parse_sample(
    subject: str = typer.Option(..., help="Email subject to classify."),
    sender: str = typer.Option("recruiting@example.com", help="Email sender."),
    body: str = typer.Option("", help="Email body/snippet."),
    save: bool = typer.Option(False, help="Persist the parsed event to data/email-events.jsonl."),
) -> None:
    parsed = ParsedEmail(
        message_id="sample",
        thread_id=None,
        sender=sender,
        subject=subject,
        snippet=body[:240],
        body=body,
        date=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    event = classify_email_event(parsed)
    if save:
        EmailEventRepository().append(event)
    console.print_json(event.model_dump_json())


@email_app.command("import-events")
def email_import_events(
    apply: bool = False,
    limit: int = 20,
    import_new: bool = False,
    new_only: bool = False,
    skip_review: bool = False,
) -> None:
    result = reconcile_email_events(
        apply=apply,
        limit=limit,
        import_new=import_new,
        update_existing=not new_only,
        create_review=not skip_review,
    )
    if result.scanned < result.total_available:
        console.print(
            f"[yellow]Scanned: {result.scanned} of {result.total_available} "
            f"(partial — pass --limit to scan more)[/yellow]"
        )
    else:
        console.print(f"Scanned: {result.scanned} of {result.total_available} (complete)")
    console.print(f"Matched existing: {result.matched}")
    console.print(f"Updated existing: {result.updated}")
    if result.regressed:
        console.print(
            f"[yellow]Held back, would move backward: {result.regressed}[/yellow]"
        )
    if result.unranked:
        console.print(
            f"[yellow]Held back, status not ranked: {result.unranked}[/yellow]"
        )
    console.print(f"Imported new: {result.imported}")
    console.print(f"Review created: {result.review_created}")
    console.print(f"Skipped: {result.skipped}")


@email_app.command("reconcile")
def email_reconcile(
    apply: bool = False,
    limit: int = 100_000,
    import_new: bool = False,
    new_only: bool = False,
    skip_review: bool = False,
) -> None:
    # `email_import_events` is a Typer command, so an omitted argument keeps its
    # OptionInfo default rather than None — every parameter is passed explicitly.
    email_import_events(
        apply=apply,
        limit=limit,
        import_new=import_new,
        new_only=new_only,
        skip_review=skip_review,
    )


@email_app.command("review-candidates")
def email_review_candidates(limit: int = 50) -> None:
    events = list_review_candidates(limit=limit)
    table = Table("ID", "Type", "Company", "Role", "Confidence", "Subject")
    table.columns[0].no_wrap = True
    for event in events:
        table.add_row(
            event.id[:12],
            event.event_type,
            _short(event.company or "", 28),
            _short(event.role or "", 44),
            f"{event.confidence:.2f}",
            _short(event.subject, 56),
        )
    console.print(table)


@email_app.command("approve-event")
def email_approve_event(
    event_id: str,
    company: str | None = None,
    role: str | None = None,
    status: str | None = None,
    force_new: bool = False,
    note: str = "Approved from Gmail review",
) -> None:
    try:
        result = approve_email_event(
            event_id,
            company=company,
            role=role,
            status=status,
            force_new=force_new,
            note=note,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    if result.tracker_entry:
        entry = result.tracker_entry
        console.print(f"Imported tracker row #{entry.number}: {entry.company} / {entry.role} -> {entry.status}")
        return
    if result.matched_existing:
        entry = result.matched_existing
        console.print(
            f"Matched existing tracker row #{entry.number}: {entry.company} / {entry.role} "
            f"({result.match_score:.2f})"
        )


@email_app.command("ignore-event")
def email_ignore_event(event_id: str, note: str = "") -> None:
    try:
        event = ignore_email_event(event_id, note=note)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    console.print(f"Ignored email event {event.id[:12]}: {event.company or '?'} / {event.role or '?'}")


@review_app.command("list")
def review_list(limit: int = 20, status: str = "open") -> None:
    items = ReviewRepository().list(limit=limit, status=status)
    table = Table("ID", "Type", "Priority", "Status", "Summary")
    for item in items:
        table.add_row(item.id[:12], item.type, item.priority, item.status, item.summary)
    console.print(table)
