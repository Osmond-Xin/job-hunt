"""Tier 9: Getro-powered accelerator and VC portfolio job boards.

Why this exists: the operator's own read of his record, 2026-09-17 — "我很适合
初创企业。我的工作简历和背景也都是初创企业的" — and the boards where early-stage
Canadian companies actually post are not the national aggregators. They are the
portfolio boards of the funds and hubs that back them: Communitech, MaRS,
Inovia, Real Ventures, Antler. Those five (and most of the others found that
day) run on Getro, which means one reader covers all of them.

The API behind every Getro board::

    POST https://api.getro.com/api/v2/collections/<network_id>/search/jobs
    Accept: application/json
    {"hitsPerPage": 20, "page": 0, "query": "ai engineer",
     "filters": {"searchable_locations": ["Canada"]}}

    -> {"results": {"count": 1183, "jobs": [{title, url, created_at,
        searchable_locations, organization: {name}, work_mode, seniority, ...}]}}

Two measured facts shape the design (2026-09-17, against Communitech):

- **`hitsPerPage` is ignored**: 20 rows a page whatever you ask for. Communitech
  alone advertises 1,183 jobs, so paging a whole board is 60 requests for a list
  that is mostly not his work — the board carries EY tax lawyers next to ML
  engineers. The tier therefore searches with a list of role queries instead of
  walking the board, and caps pages per query.
- **`filters.job_functions` matches nothing** (the facet wants values this
  module has no way to enumerate), while `filters.searchable_locations` works:
  "Canada" narrowed Communitech from 1,183 to 1,007. Location is filtered
  server-side; the occupation is left to the query and to the caller's title
  filter.

Unlike the Chinese boards (tier 8), these titles are in English and the boards
are general — so scan.py runs these rows through the **positive** title filter
rather than waving them past it.

The network id is not the board's slug: it is the integer in
``props.pageProps.network.id`` of the board's own HTML. Recorded per board in
``portals.yml`` so this module never has to scrape it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row

GETRO_SEARCH = "https://api.getro.com/api/v2/collections/{network_id}/search/jobs"
_PAGE_SIZE = 20
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def _posted(created_at: Any) -> str:
    """``created_at`` is a unix timestamp; the pipeline stores ISO dates."""
    try:
        return datetime.fromtimestamp(float(created_at), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _location(job: dict[str, Any]) -> str:
    """The most specific place the board names.

    Getro returns a widening list — "Toronto, ON, Canada", "Ontario, Canada",
    "Canada" — and the last of those is the one `triage.region` cannot place.
    """
    places = [str(p).strip() for p in (job.get("searchable_locations") or job.get("locations") or []) if str(p).strip()]
    if not places:
        return ""
    return max(places, key=lambda place: place.count(","))


def _search(
    client: httpx.Client,
    network_id: int,
    query: str,
    page: int,
    locations: list[str],
    timeout: float,
) -> dict[str, Any]:
    body: dict[str, Any] = {"hitsPerPage": _PAGE_SIZE, "page": page, "query": query, "filters": {}}
    if locations:
        body["filters"] = {"searchable_locations": locations}
    response = client.post(
        GETRO_SEARCH.format(network_id=network_id),
        json=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("results") or {}


def scan_getro_boards_source(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    """Early-stage postings from every configured Getro board, as one source."""
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="getro_boards", ok=True))

    boards = [board for board in (config.get("boards") or []) if board.get("enabled", True)]
    queries = [str(q).strip() for q in (config.get("queries") or []) if str(q).strip()]
    locations = [str(place) for place in (config.get("locations") or [])]
    max_pages = max(1, int(config.get("max_pages", 3)))
    delay = float(config.get("delay_s", 0.5))
    timeout = float(config.get("timeout_s", 30.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=timeout, follow_redirects=True)

    postings: list[JobPosting] = []
    seen_urls: set[str] = set()
    errors = 0
    truncated = False
    advertised = 0
    try:
        for board in boards:
            network_id = board.get("network_id")
            if not network_id:
                errors += 1
                continue
            board_id = str(board.get("id") or network_id)
            for query in queries:
                for page in range(max_pages):
                    try:
                        results = _search(client, int(network_id), query, page, locations, timeout)
                    except Exception:  # noqa: BLE001 - one failed page is not a quiet board
                        errors += 1
                        break
                    jobs = results.get("jobs") or []
                    count = int(results.get("count") or 0)
                    if page == 0:
                        advertised += count
                    for job in jobs:
                        url = str(job.get("url") or "").strip()
                        if not url or url in seen_urls:
                            continue
                        posting = from_row(
                            {
                                "url": url,
                                "title": str(job.get("title") or "").strip(),
                                "company": str((job.get("organization") or {}).get("name") or "").strip(),
                                "location": _location(job),
                                "posted": _posted(job.get("created_at")),
                                "source": f"getro:{board_id}",
                            },
                            source_id=f"getro:{board_id}",
                            portal="getro",
                        )
                        if posting is None:
                            continue
                        seen_urls.add(url)
                        postings.append(posting)
                    if len(jobs) < _PAGE_SIZE or (page + 1) * _PAGE_SIZE >= count:
                        break
                    if page + 1 == max_pages:
                        truncated = True
                    if delay:
                        sleep(delay)
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="getro_boards",
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised or None,
            truncated=truncated,
            errors=errors,
        ),
    )
