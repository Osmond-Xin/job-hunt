"""Tier 16: Himalayas, a board of remote roles, filtered to ones open to Canada.

The public search API needs no key::

    GET https://himalayas.app/jobs/api/search?country=Canada&q=data+analyst&page=N
    -> {"totalCount": 327, "limit": 20,
        "jobs": [{title, companyName, guid, pubDate, locationRestrictions, ...}]}

``country=Canada`` keeps roles whose location restrictions include Canada:
7,840 of them on 2026-09-27, far too many to walk, so the tier searches by the
profile's role terms (``q``) as Adzuna, WorkBC and Workable do. 20 rows a
page; ``page`` pages this endpoint (``offset`` is ignored here, measured).
Every row is remote, so its location is written "Remote, Canada". A general
board: the positive title filter applies.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row

API = "https://himalayas.app/jobs/api/search"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def parse_job(job: dict[str, Any]) -> dict[str, str]:
    try:
        posted = datetime.fromtimestamp(float(job.get("pubDate")), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        posted = ""
    return {
        "url": str(job.get("applicationLink") or job.get("guid") or "").strip(),
        "title": str(job.get("title") or "").strip(),
        "company": str(job.get("companyName") or "").strip(),
        "location": "Remote, Canada",
        "posted": posted,
        "source": "himalayas",
    }


def scan_himalayas_source(
    config: dict[str, Any] | None,
    roles: list[str],
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="himalayas", ok=True))
    queries = [str(r).strip() for r in (config.get("queries") or roles or []) if str(r).strip()]
    if not queries:
        return SourceResult(
            postings=[],
            health=SourceHealth(source_id="himalayas", ok=False, note="enabled, but no role terms to search"),
        )
    max_pages = max(1, int(config.get("max_pages", 3)))
    delay = float(config.get("delay_s", 1.0))

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
    first = True
    try:
        for query in queries:
            total = 0
            query_urls: set[str] = set()
            ended = "budget"
            for page in range(1, max_pages + 1):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                try:
                    response = client.get(API, params={"country": "Canada", "q": query, "page": page})
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    errors += 1
                    ended = "error"
                    break
                if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                    errors += 1
                    ended = "error"
                    break
                total = total or int(payload.get("totalCount") or 0)
                jobs = payload["jobs"]
                before = len(query_urls)
                for job in jobs:
                    row = parse_job(job) if isinstance(job, dict) else None
                    posting = from_row(row, source_id="himalayas", portal="himalayas") if row else None
                    if posting is None:
                        continue
                    query_urls.add(posting.url)
                    if posting.url in seen:
                        continue
                    seen.add(posting.url)
                    postings.append(posting)
                listed = len(query_urls)
                fresh = listed - before
                # Not a short page: non-final pages sometimes carry 19 rows
                # (2026-09-27), so only an empty or repeated page, or the
                # count, ends a search.
                if not fresh or listed >= total:
                    ended = "done"
                    break
            advertised += total
            if ended == "budget":
                truncated = True
            elif ended == "done" and total and len(query_urls) < total:
                short.append(f"{query} {len(query_urls)} of {total}")
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="himalayas",
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised or None,
            truncated=truncated,
            errors=errors,
            incomplete=bool(short),
            incomplete_detail="; ".join(short),
        ),
    )
