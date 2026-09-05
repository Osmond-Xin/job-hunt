from __future__ import annotations

import asyncio
from pathlib import Path
import typer
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.llm.base import ChatMessage
from job_hunt.services.llm.factory import build_cheap_provider
from job_hunt.services.llm.traced import traced_chat
from job_hunt.services import pipeline_inbox, tracker_ops

from ._render import console
from . import app, tracker_app, pipeline_app
from .evaluation import evaluate


@tracker_app.command("init")
def tracker_init(path: Path = Path("data/applications.md")) -> None:
    repo = TrackerRepository(path)
    repo.ensure_exists()
    console.print(f"[green]Tracker ready:[/green] {path}")


@tracker_app.command("stats")
def tracker_stats(path: Path = Path("data/applications.md")) -> None:
    repo = TrackerRepository(path)
    stats = repo.stats()
    console.print(f"Total: {stats['total']}")
    console.print(f"With PDF: {stats['with_pdf']}")
    table = Table("Status", "Count")
    for status, count in sorted(stats["by_status"].items()):
        table.add_row(status, str(count))
    console.print(table)


@tracker_app.command("verify")
def tracker_verify(
    path: Path = Path("data/applications.md"),
    additions_dir: Path = typer.Option(
        Path("data/tracker-additions"), help="Tracker TSV staging directory."
    ),
    reports_dir: Path = typer.Option(
        None, help="Resolve report links against this directory (skip if omitted)."
    ),
) -> None:
    result = tracker_ops.verify_pipeline(
        applications_md=path,
        additions_dir=additions_dir,
        reports_dir=reports_dir,
    )
    console.print(f"Entries: {result.entries}")
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    for error in result.errors:
        console.print(f"[red]error:[/red] {error}")
    if result.errors:
        raise typer.Exit(1)
    console.print("[green]Tracker verification passed.[/green]")


@tracker_app.command("merge")
def tracker_merge(
    additions_dir: Path = Path("data/tracker-additions"),
    path: Path = Path("data/applications.md"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report actions without writing."),
) -> None:
    """Merge pending TSV additions into applications.md."""
    result = tracker_ops.merge(additions_dir=additions_dir, applications_md=path, dry_run=dry_run)
    if not result.actions and not result.added and not result.updated:
        console.print("[green]No pending additions.[/green]")
        return
    for action in result.actions:
        if action.kind == "added":
            console.print(f"[green]+ #{action.number}[/green] {action.company} — {action.role} ({action.detail})")
        elif action.kind == "updated":
            console.print(f"[cyan]~ #{action.number}[/cyan] {action.company} — {action.role} ({action.detail})")
        else:
            console.print(f"[dim]· skip[/dim] {action.company} — {action.role} ({action.detail})")
    console.print(
        f"Summary: +{result.added} added, ~{result.updated} updated, ·{result.skipped} skipped"
        + (" (dry-run)" if dry_run else "")
    )


@tracker_app.command("dedup")
def tracker_dedup(
    path: Path = Path("data/applications.md"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report actions without writing."),
) -> None:
    """Remove duplicate (company, role) entries; promote status if a duplicate is further along."""
    result = tracker_ops.dedup(applications_md=path, dry_run=dry_run)
    for kept_num, old_status, new_status in result.promoted:
        console.print(f"[cyan]promoted #{kept_num}[/cyan] {old_status} → {new_status}")
    for entry in result.removed_entries:
        console.print(f"[yellow]drop #{entry.number}[/yellow] {entry.company} — {entry.role} ({entry.score})")
    console.print(f"Removed: {result.removed}" + (" (dry-run)" if dry_run else ""))


@tracker_app.command("normalize")
def tracker_normalize(
    path: Path = Path("data/applications.md"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report actions without writing."),
) -> None:
    """Rewrite status field of every row to the canonical label per states.yml."""
    result = tracker_ops.normalize_statuses(applications_md=path, dry_run=dry_run)
    for num, old, new in result.changed_entries:
        console.print(f"#{num}: {old!r} → {new!r}")
    for num, raw in result.unknowns:
        console.print(f"[yellow]unknown[/yellow] #{num}: {raw!r}")
    console.print(f"Changes: {result.changes}" + (" (dry-run)" if dry_run else ""))


@pipeline_app.command("add")
def pipeline_add(
    url: str = typer.Argument(..., help="Job posting URL to drop in the inbox."),
    company: str = typer.Option("", help="Company name (optional, helps tracker dedup)."),
    role: str = typer.Option("", help="Role title (optional)."),
    path: Path = typer.Option(Path("data/pipeline.md"), help="Pipeline file path."),
) -> None:
    """Append a URL to the pending inbox in pipeline.md."""
    if pipeline_inbox.add(url, company=company, role=role, path=path):
        console.print(f"[green]+ pending[/green] {url}")
    else:
        console.print(f"[yellow]Already in pipeline:[/yellow] {url}")


@pipeline_app.command("list")
def pipeline_list(
    status: str = typer.Option("all", help="Filter: all / pending / processed / error."),
    path: Path = typer.Option(Path("data/pipeline.md"), help="Pipeline file path."),
) -> None:
    """List entries in pipeline.md (default: all)."""
    status_filter = None
    if status != "all":
        try:
            status_filter = pipeline_inbox.EntryStatus(status)
        except ValueError:
            console.print(f"[red]Unknown status:[/red] {status}")
            raise typer.Exit(1) from None
    entries = pipeline_inbox.list_entries(status=status_filter, path=path)
    if not entries:
        console.print("[dim]No entries.[/dim]")
        return
    for e in entries:
        marker = {
            pipeline_inbox.EntryStatus.PENDING: "[ ]",
            pipeline_inbox.EntryStatus.PROCESSED: "[x]",
            pipeline_inbox.EntryStatus.ERROR: "[!]",
        }[e.status]
        head = f"#{e.tracker_id} | " if e.tracker_id else ""
        suffix_parts = [p for p in (e.score, f"PDF {e.pdf_check}".strip() if e.pdf_check else "", e.note) if p]
        suffix = (" | " + " | ".join(suffix_parts)) if suffix_parts else ""
        console.print(f"{marker} {head}{e.url} | {e.company} | {e.role}{suffix}")


@pipeline_app.command("process")
def pipeline_process(
    limit: int = typer.Option(0, help="Process at most N entries (0 = all)."),
    cover_letter: bool | None = typer.Option(
        None,
        "--cover-letter/--no-cover-letter",
        help="Pass-through to evaluate; defaults to apply.cover_letter_default.",
    ),
    path: Path = typer.Option(Path("data/pipeline.md"), help="Pipeline file path."),
) -> None:
    """Run `evaluate` on each pending URL, then move it to Processed (or Error)."""
    pending = pipeline_inbox.list_entries(
        status=pipeline_inbox.EntryStatus.PENDING, path=path
    )
    if not pending:
        console.print("[green]No pending entries.[/green]")
        return
    if limit > 0:
        pending = pending[:limit]

    for entry in pending:
        console.print(f"\n[bold]→[/bold] Evaluating {entry.url}")
        try:
            # `evaluate` is a Typer command, so an omitted argument keeps its
            # OptionInfo default rather than None — every parameter is passed explicitly.
            evaluate(target=entry.url, source_type="auto", trace=None, cover_letter=cover_letter)
        except SystemExit as exc:
            # evaluate() may exit on cv-sync-check errors etc; treat as inbox error.
            note = f"evaluate exited (code={exc.code})"
            pipeline_inbox.mark_error(entry.url, note=note, path=path)
            console.print(f"[red]error:[/red] {note}")
            continue
        except Exception as exc:  # noqa: BLE001 — surface unexpected failures into inbox
            note = str(exc)[:200]
            pipeline_inbox.mark_error(entry.url, note=note, path=path)
            console.print(f"[red]error:[/red] {note}")
            continue

        # Look up the freshly-written tracker row by URL match (best-effort).
        tracker = TrackerRepository(Path("data/applications.md"))
        latest = sorted(tracker.parse(), key=lambda e: e.number, reverse=True)
        match = next((e for e in latest if entry.url in e.report or entry.url in e.notes), None)
        if match:
            pdf_check = "✅" if "✅" in match.pdf else "❌"
            pipeline_inbox.mark_processed(
                entry.url,
                tracker_id=match.number,
                score=match.score,
                pdf_check=pdf_check,
                company=match.company,
                role=match.role,
                path=path,
            )
            console.print(f"[green]✓ processed[/green] #{match.number} {match.company} — {match.role}")
        else:
            pipeline_inbox.mark_error(
                entry.url, note="evaluate finished but tracker row not found", path=path
            )


@tracker_app.command("dashboard")
def tracker_dashboard(
    path: Path = typer.Option(Path("data/applications.md"), help="Tracker source file."),
    output: Path = typer.Option(
        Path("data/dashboard.html"),
        help="Output HTML path. Open in a browser after generation.",
    ),
) -> None:
    """Generate a self-contained HTML dashboard from the tracker."""
    from job_hunt.services import dashboard_html

    count = dashboard_html.generate(apps_path=path, output_path=output)
    console.print(f"[green]Wrote[/green] {output} ({count} applications)")


@tracker_app.command("check-sync")
def tracker_check_sync() -> None:
    """Run the cv/profile/prompts/digest consistency check."""
    from job_hunt.services import cv_sync_check

    result = cv_sync_check.run()
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    for error in result.errors:
        console.print(f"[red]error:[/red] {error}")
    if result.errors:
        raise typer.Exit(1)
    console.print("[green]CV sync check passed.[/green]")


@app.command("compare")
def compare_offers(
    tracker_ids: list[int] = typer.Argument(
        ..., help="Tracker entry numbers to compare (2 or more)."
    ),
    output: Path | None = typer.Option(
        None, help="Optional path to write the comparison markdown. Stdout always prints."
    ),
    max_tokens: int = typer.Option(2400, help="LLM max tokens for the comparison."),
) -> None:
    """Compare 2+ offers from the tracker on a 10-dimension weighted matrix."""
    if len(tracker_ids) < 2:
        console.print("[red]compare requires at least 2 tracker IDs.[/red]")
        raise typer.Exit(1)

    from job_hunt.services import compare_offers as svc

    offers, missing = svc.load_offers(tracker_ids)
    if missing:
        console.print(f"[yellow]Tracker IDs not found:[/yellow] {', '.join(missing)}")
    if len(offers) < 2:
        console.print("[red]Fewer than 2 offers resolved; aborting.[/red]")
        raise typer.Exit(1)

    prompt = svc.render_prompt(offers)
    settings = load_settings()
    provider = build_cheap_provider(settings)

    async def run() -> str:
        result = await traced_chat(
            provider,
            settings=settings,
            messages=[ChatMessage(role="user", content=prompt)],
            model=settings.llm.cheap.model,
            node_name="compare_offers",
            graph_name="compare_offers_cli",
            model_tier="cheap",
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return result.content

    body = asyncio.run(run())
    console.print(body)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
        console.print(f"\n[green]Wrote[/green] {output}")


@app.command("checkup")
def checkup(
    days: int = typer.Option(30, help="How far back to look for unrecorded work."),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero when anything needs attention."),
) -> None:
    """Run before you finish: what got done but never recorded?

    Building a résumé and submitting it are two steps; writing the tracker row
    is a third, and nothing forces it. This looks for the evidence that a third
    step was skipped — materials on disk with no row, acknowledgements with no
    row, rows whose mail says they are closed, follow-ups coming due.
    """
    from job_hunt.services.checkup import run_checkup

    checks = run_checkup(days=days)
    problems = [check for check in checks if not check.ok]

    for check in checks:
        mark = "[green]OK  [/green]" if check.ok else "[yellow]LOOK[/yellow]"
        console.print(f"{mark} {check.name}: {check.detail}")
        for item in check.items[:20]:
            console.print(f"       {item}")
        if len(check.items) > 20:
            console.print(f"       … and {len(check.items) - 20} more")

    if not problems:
        console.print("\n[green]Nothing outstanding.[/green]")
        return

    console.print("")
    for check in problems:
        if check.fix:
            console.print(f"[dim]{check.name} →[/dim] {check.fix}")
    if strict:
        raise typer.Exit(1)
