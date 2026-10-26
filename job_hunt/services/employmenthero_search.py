"""Tier 18: Employment Hero's cross-employer job search, Canada.

Employment Hero bought CareerBeacon, the Atlantic Canada board, and its
Canadian search (employmenthero.com/en-ca/jobs) carries those postings: 32,060
in the last 30 days on 2026-10-21, most of them from small employers in Nova
Scotia, New Brunswick, PEI and Newfoundland that no other tier reaches. The
employer_boards tier reads only Amii's widget on the same back end.

The page's own script calls a WordPress proxy that wants a nonce::

    GET https://employmenthero.com/en-ca/wp-admin/admin-ajax.php?action=eh_get_nonce
    -> {"nonce": "9346570aab"}
    GET https://employmenthero.com/en-ca/wp-json/eh-api/v1/jobs
        ?page_index=N&item_per_page=50&sort=relevance&date_posted=30
        &country_code=CA&query=<role term>          (header X-WP-Nonce)
    -> {"data": {"items": [{id, title, organisation_name, vendor_location_name,
                            created_at, remote, country_code, ...}],
                 "total_items": 676, "total_pages": 10, "current_page": 1}}

Measured 2026-10-21: no nonce is a 401; ``item_per_page`` above 50 is a 400;
``sort`` must be ``relevance`` or ``date_posted``; ``total_pages`` stops at 10
and page 11 repeats page 10, so a search reaches at most 500 rows — a count
above that is truncation. ``query`` matches loosely: "Forward Deployed
Engineer" counts 1,726, nearly every engineering posting. Sorted by relevance
the real matches come first and by page 5 it is electrical and civil
engineers, so the tier reads the first ``max_pages`` (3) pages of each term in
relevance order rather than the newest 500. A general board: positive title
filter.

``id`` is the id Amii's widget links (jobs.employmenthero.com/jobs/<id>, which
redirects to the posting page), so the two tiers dedupe on URL. Locations
carry placeholder postal codes ("Toronto, Ontario M3K 1A1"); they are dropped.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.board_queries import board_queries
from job_hunt.services.row_guard import guard_rows, safe_total

NONCE_URL = "https://employmenthero.com/en-ca/wp-admin/admin-ajax.php"
JOBS_API = "https://employmenthero.com/en-ca/wp-json/eh-api/v1/jobs"
JOB_URL = "https://jobs.employmenthero.com/jobs/{id}"
PAGE_SIZE = 50
SOURCE_ID = "employmenthero_search"
_POSTAL_RE = re.compile(r"\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d$")
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def parse_job(job: dict[str, Any]) -> dict[str, str]:
    place = _POSTAL_RE.sub("", str(job.get("vendor_location_name") or "").strip())
    if job.get("remote") is True:
        place = f"Remote, {place}" if place else "Remote"
    job_id = job.get("id")
    return {
        "url": JOB_URL.format(id=job_id) if isinstance(job_id, str) and job_id.strip() else "",
        # Some titles arrive HTML-escaped ("Senior Business Analyst &ndash; Pension").
        "title": html.unescape(str(job.get("title") or "")).strip(),
        "company": html.unescape(str(job.get("organisation_name") or "")).strip(),
        "location": place,
        "posted": str(job.get("created_at") or "")[:10],
        "source": SOURCE_ID,
    }


def _fetch_nonce(client: httpx.Client) -> str:
    try:
        response = client.get(NONCE_URL, params={"action": "eh_get_nonce"})
        response.raise_for_status()
        nonce = response.json().get("nonce")
    except (httpx.HTTPError, ValueError, AttributeError):
        return ""
    return nonce.strip() if isinstance(nonce, str) else ""


def scan_employmenthero_source(
    config: dict[str, Any] | None,
    roles: list[str],
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id=SOURCE_ID, ok=True))
    queries = board_queries(config, roles)
    if not queries:
        return SourceResult(
            postings=[],
            health=SourceHealth(source_id=SOURCE_ID, ok=False, note="enabled, but no role terms to search"),
        )
    max_pages = max(1, int(config.get("max_pages", 3)))
    delay = float(config.get("delay_s", 1.0))
    days = str(int(config.get("days", 30)))

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=float(config.get("timeout_s", 30.0)),
            follow_redirects=True,
        )

    postings: list[JobPosting] = []
    seen: set[str] = set()
    errors = 0
    truncated = False
    short: list[str] = []
    advertised = 0
    try:
        nonce = _fetch_nonce(client)
        if not nonce:
            return SourceResult(
                postings=[],
                health=SourceHealth(source_id=SOURCE_ID, ok=False, errors=1, note="no nonce from admin-ajax"),
            )
        refreshed = False
        first = True
        for query in queries:
            total: int | None = None
            pages: int | None = None
            query_urls: set[str] = set()
            lost = 0
            ended = "budget"
            page = 1
            retried = False
            while page <= max_pages:
                if not first and delay > 0:
                    sleep(delay)
                first = False
                params = {
                    "page_index": page, "item_per_page": PAGE_SIZE, "sort": config.get("sort", "relevance"),
                    "date_posted": days, "country_code": "CA", "query": query,
                }
                try:
                    response = client.get(JOBS_API, params=params, headers={"X-WP-Nonce": nonce})
                    # A nonce lives 12-24 h; fetch a new one once per run.
                    if response.status_code in (401, 403) and not refreshed:
                        refreshed = True
                        nonce = _fetch_nonce(client) or nonce
                        continue
                    response.raise_for_status()
                    data = response.json().get("data")
                except (httpx.HTTPError, ValueError, AttributeError):
                    data = None
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    # Two of four live runs on 2026-10-21 lost 2-3 pages to
                    # failures that did not recur on the next request.
                    if not retried:
                        retried = True
                        continue
                    errors += 1
                    ended = "error"
                    break
                retried = False
                if total is None:
                    total = safe_total(data.get("total_items"))
                    pages = safe_total(data.get("total_pages"))
                rows, page_lost = guard_rows(data["items"], parse_job)
                before = len(query_urls)
                for row in rows:
                    posting = from_row(row, source_id=SOURCE_ID, portal="employmenthero")
                    if posting is None:
                        continue
                    query_urls.add(posting.url)
                    if posting.url in seen:
                        continue
                    seen.add(posting.url)
                    postings.append(posting)
                if len(query_urls) > before or not rows:
                    lost += page_lost
                # Page 11 repeats page 10: a page with nothing new, the last
                # advertised page, or a reached count ends the search.
                if (
                    len(query_urls) == before
                    or (pages is not None and page >= pages)
                    or (total is not None and len(query_urls) >= total)
                ):
                    ended = "done"
                    break
                page += 1
            advertised += total or 0
            if total is not None and len(query_urls) + lost < total and ended != "error":
                if ended == "budget" or (pages is not None and pages * PAGE_SIZE < total):
                    truncated = True
                else:
                    short.append(f"{query} {len(query_urls)} of {total}")
            elif ended == "budget" and total is None:
                truncated = True
            if lost:
                short.append(f"{query}: {lost} row(s) could not be parsed")
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id=SOURCE_ID,
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised or None,
            truncated=truncated,
            errors=errors,
            incomplete=bool(short),
            incomplete_detail="; ".join(short),
        ),
    )
