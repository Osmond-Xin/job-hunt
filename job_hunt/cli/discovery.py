from __future__ import annotations

from pathlib import Path
import typer
from rich.table import Table
from job_hunt.config.models import load_settings
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.scan import scan_portals

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
    second_look: int = typer.Option(
        0,
        "--second-look",
        help=(
            "After ranking, re-check up to N rows that were CUT — hidden by one-role-per-employer, "
            "excluded as a large employer, buried under a generic title, or discarded by the scan's "
            "title filter — by reading each posting's text. Prints the ones the body argues for."
        ),
    ),
) -> None:
    """Rank the pipeline inbox down to a day's worth of candidates.

    Discovery outgrew reading: the inbox is thousands of rows, so without a
    ranking the real filter is "whatever is near the bottom of the file".
    Scoring is deterministic — role shape, then freshness, then direct
    employer over aggregator — so the same inbox always yields the same
    list. Location plays no part in it.
    """
    from job_hunt.services.balance import MIN_SCORE
    from job_hunt.services.read_log import settled_urls
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
        settled=settled_urls(),
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

    best_count = sum(1 for entry in result.entries if not entry.overflow)
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
        f"showing top {best_count}{verify_note}[/dim]"
    )
    # Balance is printed on every run, not on request: the list collapsed into
    # Greater Toronto private sector for days before anyone noticed.
    console.print(f"[dim]balance  shown: {result.mix_shown.line()}[/dim]")
    console.print(f"[dim]         inbox: {result.mix_pool.line()} (rows scoring ≥ {MIN_SCORE:g})[/dim]")
    for gap in result.reservation_gaps:
        if gap.sourcing:
            cause = (
                f"the inbox has only {gap.in_inbox} such row(s) scoring ≥ {MIN_SCORE:g} — a sourcing gap; "
                "the daily free-tier scan should fill it, see logs/daily-scan.log"
            )
        else:
            cause = (
                f"the inbox has {gap.in_inbox}, so the rest were lost to the model screen or "
                "to link verification, not to sourcing"
            )
        console.print(
            f"[yellow]balance: wanted {gap.wanted} {gap.reservation} slot(s), filled {gap.got} — {cause}[/yellow]"
        )
    console.print()
    # Rows come lane by lane (balance.balanced_slate): read from the top.
    columns = ["#", "Lane", "Score", "Company", "Role", "Location", "Posted", "Why"]
    if llm_screen:
        columns.insert(3, "Fit")
    table = Table(*columns)
    for index, entry in enumerate(result.entries, start=1):
        item = entry.ranked
        cells = [
            str(index),
            entry.lane,
            f"{item.score:.1f}",
            _short(item.row.company, 24),
            _short(item.row.role, 34),
            _short(item.row.location, 22),
            item.row.posted or "—",
            ", ".join(item.reasons) or "—",
        ]
        if llm_screen:
            cells.insert(3, f"{entry.fit:.0f}" if entry.fit is not None else "?")
            cells[-1] = _short((entry.screen_reason if entry.fit is not None else cells[-1]) or "—", 34)
        if entry.overflow:
            cells[0] = f"L{index - best_count}"
            cells[-1] = _short(f"[overflow] {cells[-1]}", 34)
        table.add_row(*cells)
    console.print(table)
    overflow_count = len(result.entries) - best_count
    if overflow_count:
        console.print(
            f"[dim]L1–L{overflow_count} are the overflow lane: the highest deterministic-score "
            "rows the model's fit ranking pushed out. They are an exploration budget, not a "
            "recommendation — the heuristic has been wrong by 7 points.[/dim]"
        )
    console.print()
    for index, entry in enumerate(result.entries, start=1):
        item = entry.ranked
        label = str(index) if not entry.overflow else f"L{index - best_count}"
        console.print(f"[bold]{label}.[/bold] {item.row.company} — {item.row.role}")
        console.print(f"   [dim]{item.row.location or 'location not stated'}[/dim]")
        console.print(f"   {item.row.url}")
    if show_excluded and result.excluded:
        console.print("\n[dim]Filtered out:[/dim]")
        for reason, count in result.excluded.most_common():
            console.print(f"  [dim]{count:>4}  {reason}[/dim]")
    if second_look > 0:
        _print_second_look(
            pipeline, tracker_path, limit=second_look,
            shown_urls={entry.ranked.row.url for entry in result.entries},
        )
    if best_count:
        console.print("\nEvaluate one: .venv/bin/job-hunt evaluate '<url>'")


@app.command("sweep-employers")
def sweep_employers(
    limit: int = typer.Option(0, help="Sweep only the first N employers (0 = all)."),
    apply: bool = typer.Option(False, "--apply", help="Append new rows to data/pipeline.md; otherwise only print them."),
    concurrency: int = typer.Option(3, help="Parallel MiniMax calls (capped at 3)."),
    employers_path: Path = typer.Option(Path("data/designated-employers.tsv"), "--employers",
                                        help="Employer list (TSV: employer, community, province, careers_url)."),
) -> None:
    """Read each designated employer's careers page; MiniMax picks the technical postings.

    See services/employer_sweep.py. Rows land in the pipeline as
    ``source: designated_employer`` with the employer's own community as location.
    """
    import datetime as dt

    import yaml

    from job_hunt.services import employer_sweep as es
    from job_hunt.services.scan import (
        ScannedJob, _append_pipeline, _append_scan_history, _known_urls, _select_title_filter, _title_matches,
        is_job_posting_url,
    )

    employers = es.load_employers(employers_path)
    with_url = [e for e in employers if e.careers_url]
    if not with_url:
        console.print(f"[yellow]{employers_path}: {len(employers)} employers, none with a careers_url[/yellow]")
        return
    if limit:
        with_url = with_url[:limit]
    portals = Path("config/portals.yml")
    if not portals.exists():
        console.print("[red]config/portals.yml is missing — its title filter decides what is kept[/red]")
        raise typer.Exit(1)
    raw = yaml.safe_load(portals.read_text(encoding="utf-8")) or {}
    positives, negatives = _select_title_filter(raw.get("title_filter") or {}, "full")
    if not positives:
        console.print("[red]config/portals.yml has no title_filter.full positives — every row would be dropped[/red]")
        raise typer.Exit(1)

    def keep(url: str, title: str) -> bool:
        # The gates every general board passes: a posting URL and the positive
        # title filter. Negatives alone let a department page through (Desjardins'
        # "En savoir plus sur le poste de Développeur ou développeuse TI").
        return is_job_posting_url(url) and _title_matches(title, positives, negatives)

    console.print(f"[dim]sweeping {len(with_url)} of {len(employers)} employers through MiniMax…[/dim]")
    try:
        report = es.collect(with_url, known_urls=_known_urls(), keep=keep, concurrency=concurrency)
    except es.ModelUnavailable as exc:
        console.print(f"[red]{exc} — nothing fetched[/red]")
        raise typer.Exit(1)

    new = [
        ScannedJob(url=row.url, title=row.title, company=row.company, location=row.location,
                   portal="designated_employer", source="designated_employer")
        for row in report.rows
    ]
    table = Table("Employer", "Community", "Posting", "URL")
    for job in new:
        table.add_row(_short(job.company, 28), _short(job.location or "—", 22), _short(job.title, 40),
                      _short(job.url, 60))
    console.print(table)
    console.print(
        f"{len(new)} new technical row(s) · {len(report.unreadable)} employer(s) unreadable · "
        f"{len(report.partial)} read only in part"
    )
    for result in [*report.unreadable, *report.partial][:40]:
        console.print(f"  [dim]{_short(result.employer.employer, 30)}: {result.error}[/dim]")
    if apply and new:
        _append_pipeline(new)
        for job in new:
            _append_scan_history(job)
        console.print(f"appended {len(new)} row(s) to data/pipeline.md ({dt.date.today().isoformat()})")


@app.command("mark-read")
def mark_read(
    url: str = typer.Argument(..., help="The posting URL exactly as it appears in data/pipeline.md."),
    verdict: str = typer.Option(..., "--verdict", help="FIT or COLLAPSE."),
    reason: str = typer.Option(..., "--reason", help="Why, in a few words (the years line, the stack, 'expired')."),
) -> None:
    """Record a posting read by hand, so triage stops offering it (COLLAPSE) once read."""
    from job_hunt.services import read_log
    from job_hunt.services.triage import parse_pipeline

    url = url.strip()  # a pasted URL with a trailing space never matched its row
    pipeline = Path("data/pipeline.md")
    rows = parse_pipeline(pipeline.read_text(encoding="utf-8")) if pipeline.exists() else []
    row = next((row for row in rows if row.url == url), None)
    if row is None:
        console.print("[yellow]not a pending pipeline URL — recorded anyway, but triage only matches exact URLs[/yellow]")
    try:
        read_log.record(url, verdict.upper(), reason, company=row.company if row else "", role=row.role if row else "")
    except ValueError:
        console.print("[red]--verdict must be FIT or COLLAPSE. An unread page is not recorded, so it is retried.[/red]")
        raise typer.Exit(2)
    console.print(f"recorded {verdict.upper()}: {row.company + ' — ' + row.role if row else url}")


def _print_second_look(pipeline: Path, tracker_path: Path, *, limit: int, shown_urls: set[str]) -> None:
    """Re-check what the filters cut. See services/second_look.py for why."""
    import asyncio

    from job_hunt.services import second_look as sl
    from job_hunt.services.read_log import settled_urls
    from job_hunt.services.triage import parse_pipeline, submitted_employers, tracker_seen
    from job_hunt.services.web_extract import extract_url_text

    tracker_text = tracker_path.read_text(encoding="utf-8") if tracker_path.exists() else ""
    seen_urls, seen_pairs = tracker_seen(tracker_text)
    picked = sl.candidates(
        parse_pipeline(pipeline.read_text(encoding="utf-8")),
        shown_urls=shown_urls,
        seen_urls=seen_urls,
        seen_pairs=seen_pairs,
        seen_employers=submitted_employers(tracker_text),
        near_misses=sl.load_near_misses(),
        already_reviewed=sl.reviewed_urls() | settled_urls(),  # a row written off by hand is not offered again
        limit=limit,
    )
    console.print(f"\n[bold]Second look[/bold] [dim]— re-reading {len(picked)} row(s) the filters cut[/dim]")
    if not picked:
        console.print("[dim]nothing fresh was cut for a reversible reason[/dim]")
        return

    async def fetch(url: str) -> str:
        return (await asyncio.wait_for(extract_url_text(url), timeout=60)).text or ""

    findings = asyncio.run(sl.review(picked, fetch))
    sl.log_findings(findings)
    rescued = [item for item in findings if item.rescued]
    confirmed = [item for item in findings if item.kill]
    unread = [item for item in findings if item.unread]
    neutral = len(findings) - len(rescued) - len(confirmed) - len(unread)
    # "Nothing rescued" must not stand in for "nothing checked".
    console.print(
        f"[dim]{len(rescued)} the posting argues for · {len(confirmed)} cut confirmed by the posting · "
        f"{neutral} no signal either way · {len(unread)} could not be read[/dim]"
    )
    if rescued:
        table = Table("Company", "Role", "Location", "Why it was cut", "What the posting says")
        for item in rescued:
            row = item.candidate.row
            table.add_row(
                _short(row.company, 22), _short(row.role, 34), _short(row.location, 18),
                _short(item.candidate.why_cut, 30), ", ".join(item.rescue),
            )
        console.print(table)
        for item in rescued:
            console.print(f"   {item.candidate.row.company} — {item.candidate.row.role}\n   {item.candidate.row.url}")
    if confirmed:
        from collections import Counter

        tally = Counter(label for item in confirmed for label in item.kill)
        console.print("[dim]confirmed cuts: " + ", ".join(f"{n} {label}" for label, n in tally.most_common()) + "[/dim]")


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
    no_websearch: bool = typer.Option(
        False,
        "--no-websearch",
        help=(
            "Skip every Brave-backed tier (tier 2 websearch companies, tier 3 channels) "
            "and run only the quota-free sources. The daily scheduled scan uses this."
        ),
    ),
) -> None:
    from job_hunt.services.web_search import build_web_search_provider

    settings = load_settings()
    web_search_provider = None if no_websearch else build_web_search_provider(settings)
    if no_websearch:
        console.print("[dim]WebSearch tiers skipped (--no-websearch): quota-free sources only.[/dim]")
    elif web_search_provider is None:
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
