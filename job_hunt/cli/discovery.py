from __future__ import annotations

from pathlib import Path
import typer
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
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


# How many shortlist slots are reserved for immigration value.
#
# With --screen the model's fit rating is the primary sort key, so a
# territorial-government post it rates 3 can never outrank anything it rates 4.
# Immigration is the operator's second standing priority and the entire reason
# the northern and Atlantic channels exist, and on 2026-08-16 that ordering
# buried every GNWT row beneath a Nova Scotia full-stack post.
#
# The lane is deliberately small and deliberately not a re-ranking. The same day
# proved the other direction: the row with the highest deterministic score of the
# batch (8.5, an ATIPP privacy analyst) evaluated at 1.18 once a model read the
# actual JD. The heuristic knows where a job is, not what it is. So this buys a
# bounded number of looks per batch — which is exactly what learning that cost.
_IMMIGRATION_LANE_SLOTS = 2


def _immigration_lane(pool: list, shortlisted: list) -> list:
    """Highest deterministic-score rows that the fit ranking pushed off the list."""
    if not pool:
        return []
    already = {id(item) for item in shortlisted}
    overflow = [item for item in pool if id(item) not in already]
    overflow.sort(key=lambda item: -item.score)
    return overflow[:_IMMIGRATION_LANE_SLOTS]


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
    from collections import Counter

    from job_hunt.services.triage import excluded, parse_pipeline, rank, tracker_seen

    pipeline = Path("data/pipeline.md")
    if not pipeline.exists():
        console.print("[yellow]No data/pipeline.md — run: .venv/bin/job-hunt scan --apply[/yellow]")
        return

    rows = parse_pipeline(pipeline.read_text(encoding="utf-8"))
    tracker_path = Path("data/applications.md")
    seen_urls, seen_pairs = (
        tracker_seen(tracker_path.read_text(encoding="utf-8"))
        if tracker_path.exists()
        else (set(), set())
    )
    # With screening on, rank a wider pool and let the model do the cutting:
    # its judgement of role shape is better than the regexes', and dropping a
    # row before the model sees it wastes the pass.
    # Verification drops rows, so it needs spare candidates to backfill with —
    # otherwise asking for 10 and losing 6 to dead links returns 4.
    ranked_limit = pool if llm_screen else (max(limit * 4, 40) if verify else limit)
    best = rank(rows, limit=ranked_limit, seen_urls=seen_urls, seen_pairs=seen_pairs)

    screened: dict[int, object] = {}
    # Rows the fit ranking pushed out that the lane may draw from (see
    # _IMMIGRATION_LANE_SLOTS). Empty unless the model screened the pool —
    # without a fit key the list is already in deterministic-score order and
    # there is nothing for the lane to rescue.
    lane_pool: list = []
    if llm_screen and best:
        from job_hunt.services.screen import screen as run_screen

        console.print(f"[dim]screening {len(best)} rows through MiniMax…[/dim]")
        verdicts, error = run_screen(
            [(item.row.company, item.row.role, item.row.location) for item in best]
        )
        if error:
            console.print(f"[yellow]screen unavailable ({error}) — showing the ranked list unscreened[/yellow]")
        kept = [
            (item, verdicts[index])
            for index, item in enumerate(best, start=1)
            if verdicts[index].keep
        ]
        dropped_by_model = len(best) - len(kept)
        unscreened = sum(1 for _i, v in kept if not v.screened)
        # Model fit first, then the deterministic priority score as tie-break.
        kept.sort(key=lambda pair: (-pair[1].fit, -pair[0].score))
        lane_pool = [item for item, _v in kept]
        best = list(lane_pool) if verify else lane_pool[:limit]
        screened = {id(item): verdict for item, verdict in kept}
        note = f"model dropped {dropped_by_model}"
        if unscreened:
            note += f", {unscreened} kept unscreened"
        console.print(f"[dim]{note}[/dim]")

    verify_note = ""
    if verify and best:
        from job_hunt.services.link_check import SKIPPED, annotate_pipeline, check_urls

        # One batched call: `check_urls` shares a single client and only sleeps
        # *between* URLs, so feeding it one URL at a time meant no keep-alive
        # and no delay at all — the opposite of the intent.
        head = best[: max(limit * 4, 40)]
        console.print(f"[dim]verifying {len(head)} candidates…[/dim]")
        verdicts = check_urls([item.row.url for item in head], delay_s=verify_delay)
        rejected = Counter()
        survivors: list[object] = []
        for item in head:
            verdict = verdicts.get(item.row.url)
            if verdict is not None and verdict.rejects:
                rejected[verdict.status] += 1
                continue
            survivors.append(item)
        marked = annotate_pipeline(pipeline, verdicts)
        # Survivors keep their rank order; the shortfall is reported below
        # rather than silently handed back as a shorter list.
        shortfall = limit - len(survivors)
        best = survivors[:limit]
        # The lane must never resurrect a posting verification just killed, and
        # rows past `head` were never checked at all.
        if lane_pool:
            lane_pool = list(survivors)
        if rejected:
            detail = ", ".join(f"{count} {status.lower()}" for status, count in rejected.most_common())
            verify_note = f" · verification dropped {sum(rejected.values())} ({detail})"
            if marked:
                verify_note += f", {marked} marked in the pipeline"
        else:
            verify_note = " · verification found nothing dead"
        # "Nothing dead" must not stand in for "nothing checked": hosts the
        # checker declines to fetch come back SKIPPED, and reporting them as
        # clean is how a dead posting reached the shortlist (2026-08-15).
        unchecked = sum(
            1
            for item in head
            if (v := verdicts.get(item.row.url)) is not None and v.status == SKIPPED
        )
        if unchecked:
            verify_note += f" · {unchecked} not checked (host not fetched by policy)"
        # Say so when the inbox could not supply what was asked for, rather than
        # returning a short list that looks like the ranking is broken.
        if shortfall > 0:
            verify_note += f" · asked for {limit}, only {len(best)} survived verification"

    lane = _immigration_lane(lane_pool, best)
    shown = [*best, *lane]

    dropped = Counter(reason for row in rows if (reason := excluded(row)))
    console.print(
        f"[dim]{len(rows)} pending · {sum(dropped.values())} filtered out · "
        f"showing top {len(best)}{verify_note}[/dim]\n"
    )
    columns = ["#", "Score", "Company", "Role", "Location", "Posted", "Why"]
    if screened:
        columns.insert(2, "Fit")
    table = Table(*columns)
    for index, item in enumerate(shown, start=1):
        in_lane = index > len(best)
        cells = [
            str(index),
            f"{item.score:.1f}",
            _short(item.row.company, 24),
            _short(item.row.role, 34),
            _short(item.row.location, 22),
            item.row.posted or "—",
            ", ".join(item.reasons) or "—",
        ]
        if screened:
            verdict = screened.get(id(item))
            cells.insert(2, f"{verdict.fit:.0f}" if verdict and verdict.screened else "?")
            cells[-1] = _short(
                (verdict.reason if verdict and verdict.screened else cells[-1]) or "—", 34
            )
        if in_lane:
            cells[0] = f"L{index - len(best)}"
            cells[-1] = _short(f"[lane] {cells[-1]}", 34)
        table.add_row(*cells)
    console.print(table)
    if lane:
        console.print(
            f"[dim]L1–L{len(lane)} are the immigration lane: the highest deterministic-score "
            "rows the model's fit ranking pushed out. They are an exploration budget, not a "
            "recommendation — the heuristic has been wrong by 7 points.[/dim]"
        )
    console.print()
    for index, item in enumerate(shown, start=1):
        label = str(index) if index <= len(best) else f"L{index - len(best)}"
        console.print(f"[bold]{label}.[/bold] {item.row.company} — {item.row.role}")
        console.print(f"   [dim]{item.row.location or 'location not stated'}[/dim]")
        console.print(f"   {item.row.url}")
    if show_excluded and dropped:
        console.print("\n[dim]Filtered out:[/dim]")
        for reason, count in dropped.most_common():
            console.print(f"  [dim]{count:>4}  {reason}[/dim]")
    if best:
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
