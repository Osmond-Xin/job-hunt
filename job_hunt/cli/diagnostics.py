from __future__ import annotations

import asyncio
import typer
from rich.table import Table
from job_hunt.config.models import Settings, load_settings
from job_hunt.services.activity import ActivityEvent, ActivityLogger, read_activity
from job_hunt.services.llm.base import ChatMessage
from job_hunt.services.llm.factory import build_cheap_provider
from job_hunt.services.llm.traced import traced_chat
from job_hunt.services.observability import TraceManager
from job_hunt.services.web_extract import extract_url_text
from job_hunt.services.web_search import (
    CachingProvider,
    WebSearchCache,
    _resolve_cache_dir,
    build_web_search_provider,
)

from ._render import console
from . import app, trace_app, activity_app, schedule_app, llm_app


@trace_app.command("status")
def trace_status() -> None:
    manager = TraceManager(load_settings())
    status = manager.status()
    console.print(f"LangSmith enabled: {status.enabled}")
    console.print(f"Project: {status.project}")
    console.print(f"Example local trace id: {status.trace_id}")


@trace_app.command("on")
def trace_on() -> None:
    console.print("Set JOB_HUNT_LANGSMITH_ENABLED=true or edit config/settings.yml.")


@trace_app.command("off")
def trace_off() -> None:
    console.print("Set JOB_HUNT_LANGSMITH_ENABLED=false or edit config/settings.yml.")


@trace_app.command("smoke-test")
def trace_smoke_test() -> None:
    settings = load_settings()
    if not settings.observability.langsmith.enabled:
        console.print("[yellow]LangSmith is disabled. Set JOB_HUNT_LANGSMITH_ENABLED=true to send traces.[/yellow]")
        raise typer.Exit(1)

    from langsmith import traceable

    @traceable(
        name="job-hunt.langsmith_smoke_test",
        run_type="chain",
        metadata={"app": "job-hunt", "test": "smoke"},
    )
    def smoke() -> dict:
        return {"ok": True, "message": "langsmith smoke test"}

    result = smoke()
    console.print(f"Sent smoke trace to project: {settings.observability.langsmith.project}")
    console.print_json(data=result)


@activity_app.command("list")
def activity_list(
    limit: int = typer.Option(20, help="Maximum number of events to show."),
    since: str | None = typer.Option(None, help="Only show events since a duration like 1d/12h/30m or an ISO timestamp."),
) -> None:
    settings = load_settings()
    try:
        events = read_activity(settings.activity.sinks.local_log.path, limit=limit, since=since)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--since") from None
    table = Table("Time", "Level", "Type", "Summary")
    for event in events:
        table.add_row(event.ts.isoformat(), event.level, event.type, event.summary)
    console.print(table)


@activity_app.command("tail")
def activity_tail(limit: int = 20) -> None:
    # `activity_list` is a Typer command, so an omitted argument keeps its
    # OptionInfo default rather than None — every parameter is passed explicitly.
    activity_list(limit=limit, since=None)


@activity_app.command("slack-test")
def activity_slack_test() -> None:
    settings = load_settings()
    logger = ActivityLogger(settings.activity)
    logger.emit(ActivityEvent(type="activity.slack_test", level="info", summary="Slack test from job-hunt"))
    console.print("Slack test event emitted. Check local activity log and Slack config.")


@schedule_app.command("list")
def schedule_list() -> None:
    console.print("Scheduler implementation is planned for Phase 6. See docs/design.md.")


@schedule_app.command("run")
def schedule_run(job_id: str) -> None:
    console.print(f"Scheduler job placeholder: {job_id}")


@llm_app.command("cheap-test")
def llm_cheap_test(
    prompt: str = "Say ok in one short sentence.",
    max_tokens: int = 1024,
) -> None:
    settings = load_settings()
    provider = build_cheap_provider(settings)

    async def run() -> None:
        result = await traced_chat(
            provider,
            settings=settings,
            messages=[ChatMessage(role="user", content=prompt)],
            model=settings.llm.cheap.model,
            node_name="cheap_test",
            graph_name="llm_smoke_test",
            model_tier="cheap",
            temperature=settings.llm.cheap.temperature,
            max_tokens=max_tokens,
        )
        console.print(result.content)
        console.print(f"provider={result.provider} tier={result.tier}")
        console.print(f"tokens={result.total_tokens} estimated={result.usage_estimated}")

    asyncio.run(run())


@app.command("search-test")
def search_test(
    query: str = typer.Argument(..., help="Search query to send to the configured provider."),
    count: int | None = typer.Option(None, help="Override result count (default from settings)."),
    freshness: str | None = typer.Option(
        None, help="Override freshness: pd (past day) / pw (week) / pm (month) / py (year)."
    ),
) -> None:
    """Smoke-test the configured WebSearch provider (Brave by default)."""
    settings = load_settings()
    provider = build_web_search_provider(settings)
    if provider is None:
        console.print(
            "[red]No web search provider configured.[/red]\n"
            "Set `web_search.provider: brave` in config/settings.yml and "
            "BRAVE_API_KEY in .env, then retry."
        )
        raise typer.Exit(1)

    hits = provider.search(query, count=count, freshness=freshness)
    if not hits:
        console.print("[yellow]No hits returned.[/yellow]")
        return

    table = Table("#", "Title", "URL", "Age")
    for i, hit in enumerate(hits, 1):
        table.add_row(str(i), hit.title[:60], hit.url[:70], hit.age or "")
    console.print(table)
    console.print(f"\n{len(hits)} result(s) for: {query!r}")


@app.command("proxy-check")
def proxy_check(
    url: str = typer.Option(
        "https://ca.linkedin.com/jobs/view/4414954379",
        help="Proxy-only URL to test the egress path against.",
    ),
) -> None:
    """Check the scrape proxy: is one configured, does it work, and what IP does it show.

    The proxy exists for hosts this project refuses to fetch from the operator's
    own address (linkedin.com). This command answers the three questions in
    order, so a failure says which step broke instead of just "no".
    """
    import asyncio

    from job_hunt.services.web_extract import (
        ProxyRequiredError,
        _is_proxy_only_host,
        scrape_proxy,
    )

    proxy = scrape_proxy()
    if not proxy:
        console.print("[yellow]No scrape proxy configured.[/yellow]")
        console.print(
            "Set [bold]network.scrape_proxy[/bold] in profile.yml (or the "
            "JOB_HUNT_SCRAPE_PROXY env var) to a SOCKS5/HTTP endpoint such as "
            "socks5://127.0.0.1:1080.\n"
            "A Shadowsocks subscription URL will NOT work here — run a local "
            "client to turn it into a port first."
        )
        raise typer.Exit(1)

    console.print(f"proxy: [bold]{proxy}[/bold]")
    if proxy.startswith("socks"):
        try:
            import socksio  # noqa: F401
        except ImportError:
            console.print(
                "[yellow]httpx cannot use a socks5:// proxy without the 'socksio' "
                "package — install it with: uv add socksio[/yellow]\n"
                "curl and Playwright can still use it, so extraction may work "
                "while other paths fail."
            )

    # 1. Does the proxy carry ordinary traffic at all, and out of which IP?
    try:
        import httpx

        seen = httpx.get("https://api.ipify.org", proxy=proxy, timeout=30).text.strip()
        console.print(f"egress IP through proxy: [bold]{seen}[/bold]")
    except Exception as exc:
        console.print(f"[red]proxy did not carry a test request: {type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(1) from exc

    # 2. Does the guarded host actually come back with content through it?
    if not _is_proxy_only_host(url):
        console.print(f"[dim]{url} is not a proxy-only host; fetching it directly.[/dim]")
    from job_hunt.services.web_extract import extract_url_text

    try:
        result = asyncio.run(extract_url_text(url, min_chars=200))
    except ProxyRequiredError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[red]fetch failed through the proxy: {type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"fetched [bold]{len(result.text)}[/bold] chars via {result.adapter}; "
        f"title: {result.title[:80] or '(none)'}"
    )
    if len(result.text) < 1000:
        console.print(
            "[yellow]That is short enough to be a login wall or a boilerplate page "
            "rather than a job description — read it before trusting it.[/yellow]"
        )


@app.command("search-usage")
def search_usage(
    month: str | None = typer.Option(
        None,
        help="UTC month in YYYY-MM. Defaults to the current month.",
    ),
) -> None:
    """Show WebSearch quota usage (api_calls / cache_hits / errors) for a month.

    Brave's free tier is ~2k queries/month — this command lets the operator
    eyeball the counter before kicking off a wide scan or batch evaluation.
    """
    settings = load_settings()
    provider = build_web_search_provider(settings)
    cache: WebSearchCache | None = None
    if isinstance(provider, CachingProvider):
        cache = provider.cache
    else:
        cache_dir = _resolve_cache_dir(settings, "brave")
        if cache_dir.exists():
            cache = WebSearchCache(
                cache_dir,
                ttl_seconds=settings.web_search.cache_ttl_seconds,
            )
    if cache is None:
        console.print(
            "[yellow]No cache directory found.[/yellow] "
            "Either WebSearch is disabled or no queries have run yet."
        )
        return

    usage = cache.usage(month=month)
    table = Table("Metric", "Count")
    table.add_row("API calls", str(usage.api_calls))
    table.add_row("Cache hits", str(usage.cache_hits))
    table.add_row("Errors / empty", str(usage.errors))
    table.add_row(
        "[bold]Total provider lookups[/bold]",
        f"[bold]{usage.api_calls + usage.cache_hits + usage.errors}[/bold]",
    )
    console.print(f"WebSearch usage for {usage.month} (UTC):")
    console.print(table)
    console.print(f"\nUsage file: {cache.usage_path()}")
