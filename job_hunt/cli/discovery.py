from __future__ import annotations

from pathlib import Path
import typer
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.scan import scan_portals
from job_hunt.services.shortlist import _IMMIGRATION_LANE_SLOTS, _immigration_lane

from ._render import _short, console
from . import app


def _shortlist_rows(*, limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pipeline = Path("data/pipeline.md")
    if pipeline.exists():
        for line in reversed(pipeline.read_text(encoding="utf-8").splitlines()):
            if len(rows) >= limit:
                break
            if not line.startswith("- [ ]"):
                continue
            parts = [part.strip() for part in line.removeprefix("- [ ]").split("|")]
            url = parts[0] if parts and parts[0].startswith("http") else ""
            rows.append(
                {
                    "source": "pipeline",
                    "company": parts[1] if len(parts) > 1 else "",
                    "role": parts[2] if len(parts) > 2 else "",
                    "status": "pending",
                    "url": url,
                }
            )
    if len(rows) < limit:
        for entry in TrackerRepository(Path("data/applications.md")).parse():
            if len(rows) >= limit:
                break
            haystack = f"{entry.status} {entry.notes}".lower()
            if entry.status != "Evaluated" or not any(token in haystack for token in ["apply", "maybe", "hold"]):
                continue
            rows.append(
                {
                    "source": f"tracker #{entry.number}",
                    "company": entry.company,
                    "role": entry.role,
                    "status": f"{entry.score} {entry.status}",
                    "url": entry.report,
                }
            )
    return rows[:limit]


@app.command("search")
def search_jobs(
    role: str | None = typer.Option(None, help="Target role hint shown in output. Direct ATS scan still uses config/portals.yml filters."),
    location: str | None = typer.Option(None, help="Target location hint shown in output."),
    company: str | None = typer.Option(None, help="Only scan matching company name."),
    limit_companies: int | None = typer.Option(None, help="Limit number of direct ATS companies."),
    include_non_canada: bool = typer.Option(False, help="Include jobs outside Canada in results."),
    save: bool = typer.Option(True, "--save/--no-save", help="Write new jobs to data/pipeline.md and scan history."),
) -> None:
    """Begin searching configured direct ATS portals and write a shortlist inbox."""
    if role or location:
        console.print(f"Search target: {role or 'configured roles'} / {location or 'configured locations'}")
        console.print("[dim]Current scanner uses config/portals.yml title and Canada filters; role/location are onboarding hints.[/dim]")
    scan(
        company=company,
        limit_companies=limit_companies,
        include_non_canada=include_non_canada,
        apply=save,
        # `scan` is a Typer command, so an omitted argument keeps its OptionInfo
        # default rather than None, and tier-3 discovery then compares a channel
        # id against it. Every `search` run died there.
        channel=None,
    )
    console.print("\nNext: .venv/bin/job-hunt shortlist")


@app.command("shortlist")
def shortlist(limit: int = typer.Option(20, help="Number of items to show.")) -> None:
    """Show jobs that are ready for review from the local pipeline/tracker."""
    rows = _shortlist_rows(limit=limit)
    table = Table("#", "Source", "Company", "Role", "Score/Status", "URL or Report")
    for index, row in enumerate(rows, start=1):
        table.add_row(str(index), row["source"], row["company"], row["role"], row["status"], _short(row["url"], 80))
    console.print(table)
    if rows:
        console.print("\nEvaluate a URL: .venv/bin/job-hunt evaluate '<url>'")
        console.print("Prepare apply flow: .venv/bin/job-hunt loop '<url>'")
    else:
        console.print("No shortlist items found. Run: .venv/bin/job-hunt search --save")


@app.command("triage")
def triage(
    limit: int = typer.Option(10, help="How many candidates to surface."),
    show_excluded: bool = typer.Option(False, "--show-excluded", help="Also print what was filtered out and why."),
    llm_screen: bool = typer.Option(False, "--screen", help="Second pass: have MiniMax judge role shape on the cheap tier."),
    pool: int = typer.Option(60, help="With --screen, how many ranked rows to send to the model."),
    verify: bool = typer.Option(False, "--verify", help="Fetch each candidate before showing it; drop dead, expired, internal-only and talent-pool postings, and mark them in the pipeline. Every text-based rejection is confirmed twice by an independent reader before it counts."),
    verify_delay: float = typer.Option(1.0, help="Seconds between verification requests."),
) -> None:
    """Rank the pipeline inbox down to a day's worth of candidates.

    Discovery outgrew reading: the inbox is thousands of rows, so without a
    ranking the real filter is "whatever is near the bottom of the file".
    Scoring is deterministic — immigration value, then location, then role
    shape, then freshness — so the same inbox always yields the same list.
    """
    from job_hunt.services.shortlist import ShortlistOptions, build_shortlist

    pipeline = Path("data/pipeline.md")
    if not pipeline.exists():
        console.print("[yellow]No data/pipeline.md — run: .venv/bin/job-hunt scan --apply[/yellow]")
        return

    tracker_path = Path("data/applications.md")
    result = build_shortlist(
        pipeline=pipeline,
        tracker=tracker_path,
        options=ShortlistOptions(
            limit=limit, screen=llm_screen, pool=pool, verify=verify, verify_delay=verify_delay
        ),
        # --screen and --verify are each minutes of wall time; without a line
        # printed before the call starts, the operator has no signal the
        # command is alive rather than hung.
        progress=lambda message: console.print(f"[dim]{message}[/dim]"),
    )

    if llm_screen:
        if result.screen_error:
            console.print(
                f"[yellow]screen unavailable ({result.screen_error}) — "
                "showing the ranked list unscreened[/yellow]"
            )
        note = f"model dropped {result.dropped_by_model}"
        if result.unscreened:
            note += f", {result.unscreened} kept unscreened"
        console.print(f"[dim]{note}[/dim]")

    best_count = sum(1 for entry in result.entries if not entry.lane)
    verify_note = ""
    if verify:
        if result.rejected:
            detail = ", ".join(f"{count} {status.lower()}" for status, count in result.rejected.most_common())
            verify_note = f" · verification dropped {sum(result.rejected.values())} ({detail})"
            if result.marked_in_pipeline:
                verify_note += f", {result.marked_in_pipeline} marked in the pipeline"
        else:
            verify_note = " · verification found nothing dead"
        # "Nothing dead" must not stand in for "nothing checked": hosts the
        # checker declines to fetch come back SKIPPED, and reporting them as
        # clean is how a dead posting reached the shortlist (2026-08-15).
        if result.unchecked:
            verify_note += f" · {result.unchecked} not checked (host not fetched by policy)"
        # Say so when the inbox could not supply what was asked for, rather than
        # returning a short list that looks like the ranking is broken.
        if result.shortfall > 0:
            verify_note += f" · asked for {limit}, only {best_count} survived verification"

    console.print(
        f"[dim]{result.pending} pending · {sum(result.excluded.values())} filtered out · "
        f"showing top {best_count}{verify_note}[/dim]\n"
    )
    columns = ["#", "Score", "Company", "Role", "Location", "Posted", "Why"]
    if llm_screen:
        columns.insert(2, "Fit")
    table = Table(*columns)
    for index, entry in enumerate(result.entries, start=1):
        item = entry.ranked
        cells = [
            str(index),
            f"{item.score:.1f}",
            _short(item.row.company, 24),
            _short(item.row.role, 34),
            _short(item.row.location, 22),
            item.row.posted or "—",
            ", ".join(item.reasons) or "—",
        ]
        if llm_screen:
            cells.insert(2, f"{entry.fit:.0f}" if entry.fit is not None else "?")
            cells[-1] = _short((entry.screen_reason if entry.fit is not None else cells[-1]) or "—", 34)
        if entry.lane:
            cells[0] = f"L{index - best_count}"
            cells[-1] = _short(f"[lane] {cells[-1]}", 34)
        table.add_row(*cells)
    console.print(table)
    lane_count = len(result.entries) - best_count
    if lane_count:
        console.print(
            f"[dim]L1–L{lane_count} are the immigration lane: the highest deterministic-score "
            "rows the model's fit ranking pushed out. They are an exploration budget, not a "
            "recommendation — the heuristic has been wrong by 7 points.[/dim]"
        )
    console.print()
    for index, entry in enumerate(result.entries, start=1):
        item = entry.ranked
        label = str(index) if not entry.lane else f"L{index - best_count}"
        console.print(f"[bold]{label}.[/bold] {item.row.company} — {item.row.role}")
        console.print(f"   [dim]{item.row.location or 'location not stated'}[/dim]")
        console.print(f"   {item.row.url}")
    if show_excluded and result.excluded:
        console.print("\n[dim]Filtered out:[/dim]")
        for reason, count in result.excluded.most_common():
            console.print(f"  [dim]{count:>4}  {reason}[/dim]")
    if best_count:
        console.print("\nEvaluate one: .venv/bin/job-hunt evaluate '<url>'")


@app.command("scan")
def scan(
    company: str | None = typer.Option(None, help="Only scan matching company name."),
    limit_companies: int | None = typer.Option(None, help="Limit number of direct ATS companies."),
    include_non_canada: bool = typer.Option(False, help="Include jobs outside Canada in results."),
    apply: bool = typer.Option(False, help="Write new jobs to scan history and pipeline."),
    channel: str | None = typer.Option(
        None,
        "--channel",
        help=(
            "Restrict tier-3 discovery to one channel id "
            "(linkedin / indeed / glassdoor / waterlooworks / talentegg / ...)."
        ),
    ),
) -> None:
    from job_hunt.services.web_search import build_web_search_provider

    settings = load_settings()
    web_search_provider = build_web_search_provider(settings)
    if web_search_provider is None:
        console.print(
            "[dim]WebSearch tier disabled (set web_search.provider=brave + BRAVE_API_KEY "
            "to scan companies with scan_method: websearch and discovery channels).[/dim]"
        )
    result = scan_portals(
        company=company,
        limit_companies=limit_companies,
        include_non_canada=include_non_canada,
        apply=apply,
        web_search_provider=web_search_provider,
        discovery_channel=channel,
        settings=settings,
    )
    console.print(f"Scanned companies: {result.scanned_companies}")
    console.print(f"Fetched jobs: {result.fetched_jobs}")
    console.print(f"Matched jobs: {result.matched_jobs}")
    console.print(f"Skipped by filters: {result.skipped_filtered}")
    console.print(f"New jobs: {result.new_jobs}")
    console.print(f"Skipped duplicates: {result.skipped_duplicates}")
    for error in result.errors:
        console.print(f"[yellow]warning:[/yellow] {error}")
    table = Table("Company", "Title", "Location", "Portal", "URL")
    for job in result.jobs[:30]:
        table.add_row(job.company, job.title, job.location, job.portal, job.url)
    if result.jobs:
        console.print(table)
    if result.new_jobs > 30:
        console.print(f"... {result.new_jobs - 30} more new jobs")
