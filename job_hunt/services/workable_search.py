"""Tier 15: Workable's cross-employer job search (jobs.workable.com).

Tier 1 already reads a Workable employer's own widget feed when the employer
is on the tracked list. This reads the other direction: every Workable
customer's Canadian postings, found by role::

    GET https://jobs.workable.com/api/v1/jobs?query=data+analyst&location=Canada
        [&pageToken=<nextPageToken>]
    -> {"totalSize": 46, "nextPageToken": "...",
        "jobs": [{id, title, url, created, workplace,
                  location: {city, subregion, countryName}, company: {title}}]}

20 rows a page whatever is asked, measured 2026-09-27; paging is by the
opaque ``nextPageToken``. Employers are small and mid-sized — a community
health centre, a regional engineering firm — the kind the national boards
bury. A general board, so scan.py keeps the positive title filter; the
queries are the profile's role terms, as for Adzuna and WorkBC.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.row_guard import guard_rows, row_key, safe_total

_SITE = "https://jobs.workable.com"
API = f"{_SITE}/api/v1/jobs"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def parse_job(job: dict[str, Any]) -> dict[str, str]:
    place = job.get("location") if isinstance(job.get("location"), dict) else {}
    parts = [place.get("city"), place.get("subregion"), place.get("countryName")]
    location = ", ".join(str(p) for p in parts if p)
    alternatives = job.get("locations") if isinstance(job.get("locations"), list) else []
    if not location and alternatives:
        location = str(alternatives[0])
    if job.get("workplace") == "remote":
        location = f"Remote, {location}" if location else "Remote"
    company = job.get("company") if isinstance(job.get("company"), dict) else {}
    return {
        "key": row_key(job.get("id")),
        "url": job.get("url") or "",
        "title": str(job.get("title") or "").strip(),
        "company": str(company.get("title") or "").strip(),
        "location": location,
        "posted": str(job.get("created") or "")[:10],
        "source": "workable_search",
    }


def scan_workable_search_source(
    config: dict[str, Any] | None,
    roles: list[str],
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="workable_search", ok=True))
    queries = [str(r).strip() for r in (config.get("queries") or roles or []) if str(r).strip()]
    if not queries:
        return SourceResult(
            postings=[],
            health=SourceHealth(source_id="workable_search", ok=False, note="enabled, but no role terms to search"),
        )
    location = str(config.get("location") or "Canada")
    max_pages = max(1, int(config.get("max_pages", 5)))
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
            token: str | None = None
            tokens: set[str] = set()
            total: int | None = None
            ids: set[Any] = set()
            lost = 0
            ended = "budget"
            for _page in range(max_pages):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                params = {"query": query, "location": location}
                if token:
                    params["pageToken"] = token
                try:
                    response = client.get(API, params=params)
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
                if total is None:
                    total = safe_total(payload.get("totalSize"))
                jobs = payload["jobs"]
                before = len(ids)
                rows, page_lost = guard_rows(jobs, parse_job, base=_SITE)
                # Identity is the id when it is a usable one, else the URL,
                # so rows without ids still show progress.
                ids.update(row["key"] or row["url"] for row in rows)
                # A repeated page repeats its losses too; count them once.
                # (``not rows``, not ``not jobs``: a page whose rows all fail
                # validation must still count its loss.)
                if len(ids) > before or not rows:
                    lost += page_lost
                for row in rows:
                    posting = from_row(row, source_id="workable_search", portal="workable")
                    if posting is None or posting.url in seen:
                        continue
                    seen.add(posting.url)
                    postings.append(posting)
                token = payload.get("nextPageToken") if isinstance(payload.get("nextPageToken"), str) else None
                # A token seen before is a cycle, not a next page.
                if token in tokens:
                    token = None
                tokens.add(token or "")
                if not token or not jobs or len(ids) == before:
                    ended = "done"
                    break
            advertised += total or 0
            # Only a budget that left rows behind is truncation.
            if ended == "budget" and (total is None or len(ids) < total):
                truncated = True
            if lost:
                short.append(f"{query}: {lost} row(s) could not be parsed")
            elif ended == "done" and total is not None and len(ids) < total:
                short.append(f"{query} {len(ids)} of {total}")
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="workable_search",
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised or None,
            truncated=truncated,
            errors=errors,
            incomplete=bool(short),
            incomplete_detail="; ".join(short),
        ),
    )
