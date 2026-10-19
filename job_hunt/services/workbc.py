"""Tier 14: WorkBC, British Columbia's provincial job board.

WorkBC aggregates BC postings from employers' own ATS boards — Greenhouse,
Lever, Workday, Oracle, UKG, the health authorities — into one search, and
its single-page app reads a public JSON API::

    POST https://workbc-jb.a55eb5-prod.stratus.cloud.gov.bc.ca/api/Search/JobSearch
    {"Page": 1, "PageSize": 100, "Keyword": "data analyst",
     "SearchInField": "title", "SortOrder": 1}
    -> {"count": 188, "pageNumber": 1, "pageSize": 100,
        "result": [{JobId, Title, EmployerName, City, DatePosted,
                    IsFederalJob, ExternalSource: {Source: [{Url, Source}]}}]}

Measured 2026-09-27: "developer" in titles gave 188 rows; 4 had
``IsFederalJob: true`` and a numeric JobId — Job Bank postings mirrored in,
which tier 4 already reads, so they are skipped. The rest carry the
employer's own posting URL in ``ExternalSource``, which is what is recorded:
it is the page a person applies through, and it deduplicates against the
same posting found by the employer's own board.

The search is a title search per role term — the profile's target roles,
the same list Adzuna is queried with. A general board, so scan.py keeps the
positive title filter on these rows.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.row_guard import SKIP, guard_rows, row_key, safe_total

API = "https://workbc-jb.a55eb5-prod.stratus.cloud.gov.bc.ca/api/Search/JobSearch"
_PAGE_SIZE = 100
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def parse_job(job: dict[str, Any]) -> Any:
    """A pipeline row, or ``SKIP`` for a Job Bank mirror (tier 4 reads those)."""
    if job.get("IsFederalJob"):
        return SKIP
    external = job.get("ExternalSource")
    sources = external.get("Source") if isinstance(external, dict) else None
    url = next((s.get("Url") for s in (sources if isinstance(sources, list) else [])
                if isinstance(s, dict) and s.get("Url")), "")
    city = str(job.get("City") or "").strip()
    return {
        "key": row_key(job.get("JobId")),
        "url": url,
        "title": str(job.get("Title") or "").strip(),
        "company": str(job.get("EmployerName") or "").strip(),
        # WorkBC is BC-only; the city alone would leave smaller towns unplaced.
        "location": f"{city}, BC" if city else "British Columbia",
        "posted": str(job.get("DatePosted") or "")[:10],
        "source": "workbc",
    }


def scan_workbc_source(
    config: dict[str, Any] | None,
    roles: list[str],
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    """One title search per role term, walked to the reported count.

    Coverage is measured on unique JobIds per search: a page that repeats one
    does not count twice. Job Bank mirrors are skipped on purpose and counted
    as covered; a row that cannot be read is counted as lost.
    """
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="workbc", ok=True))
    queries = [str(r).strip() for r in (config.get("queries") or roles or []) if str(r).strip()]
    if not queries:
        return SourceResult(
            postings=[],
            health=SourceHealth(source_id="workbc", ok=False, note="enabled, but no role terms to search"),
        )
    max_pages = max(1, int(config.get("max_pages", 3)))
    delay = float(config.get("delay_s", 1.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=float(config.get("timeout_s", 45.0)),
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
            total: int | None = None
            ids: set[Any] = set()
            lost = 0
            ended = "budget"
            for page in range(1, max_pages + 1):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                try:
                    response = client.post(
                        API,
                        json={"Page": page, "PageSize": _PAGE_SIZE, "Keyword": query,
                              "SearchInField": "title", "SortOrder": 1},
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    errors += 1
                    ended = "error"
                    break
                if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
                    errors += 1
                    ended = "error"
                    break
                if total is None:
                    total = safe_total(payload.get("count"))
                jobs = payload["result"]
                before = len(ids)
                # Identity is the JobId when it is a usable id, else the
                # validated URL; mirrors count as covered.
                ids.update(row_key(job.get("JobId")) or f"#mirror{index}"
                           for index, job in enumerate(jobs)
                           if isinstance(job, dict) and job.get("IsFederalJob"))
                rows, page_lost = guard_rows(jobs, parse_job)
                ids.update(row["key"] or row["url"] for row in rows)
                # A repeated page repeats its losses too; count them once.
                # (``not rows``, not ``not jobs``: a page whose rows all fail
                # validation must still count its loss.)
                if len(ids) > before or not rows:
                    lost += page_lost
                for row in rows:
                    posting = from_row(row, source_id="workbc", portal="workbc")
                    if posting is None or posting.url in seen:
                        continue
                    seen.add(posting.url)
                    postings.append(posting)
                # An empty or repeated page, or reaching a known count, ends
                # the search. A short page ends it only when the count is
                # unknown: with a known count not yet reached, a short page
                # mid-walk is read past (pages of 40, 100, 10 for 150).
                if not jobs or len(ids) == before or (total is not None and len(ids) >= total):
                    ended = "done"
                    break
                if total is None and len(jobs) < _PAGE_SIZE:
                    ended = "done"
                    break
            advertised += total or 0
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
            source_id="workbc",
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised or None,
            truncated=truncated,
            errors=errors,
            incomplete=bool(short),
            incomplete_detail="; ".join(short),
        ),
    )
