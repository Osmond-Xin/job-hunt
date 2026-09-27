"""Tier 12: WorkPEI, Prince Edward Island's provincial job board.

PEI is an Atlantic Immigration Program province, and WorkPEI carries both the
island's private employers and the PEI public service (which reaches it as
syndicated rows linking out to its own ATS). The board is WordPress, and its
REST API lists every posting without a browser::

    GET https://workpei.ca/wp-json/wp/v2/job-listings?per_page=100&page=N
    -> [{id, date, link, title: {rendered}, meta: {_company_name, _filled},
         class_list: [..., "source-workpei" | "source-pei-public-service"]}]
    headers: X-WP-Total, X-WP-TotalPages

271 postings over 3 pages on 2026-09-27; a page past the end is a 400.
``_fields`` trims each row to what is read here — the full row carries the
Yoast SEO block and weighs ten times as much.

The public-service rows have no company and pack the competition into the
title: ``"174437 – Social Worker – Department of Social Development – Permanent"``.
The role is the second part and the employer is the Government of PEI.
No row carries a place, so every row is located "Prince Edward Island".

A general board — cannabis clerks, early childhood educators, social workers —
so scan.py keeps the positive title filter on it, like Getro.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row

API = "https://workpei.ca/wp-json/wp/v2/job-listings"
_FIELDS = "id,date,link,title,meta,class_list"
_PUBLIC_SERVICE = "source-pei-public-service"
_USER_AGENT = "Mozilla/5.0 (compatible; job-hunt/0.1; personal job search)"


def parse_listing(item: dict[str, Any]) -> dict[str, str] | None:
    """A pipeline row from one REST item, or None for a filled posting."""
    meta = item.get("meta") or {}
    if meta.get("_filled"):
        return None
    title = html.unescape(str((item.get("title") or {}).get("rendered") or "")).strip()
    company = str(meta.get("_company_name") or "").strip()
    if _PUBLIC_SERVICE in (item.get("class_list") or []):
        parts = [part.strip() for part in re.split(r"\s+[–-]\s+", title)]
        if len(parts) > 1 and parts[0].isdigit():
            title = parts[1]
        company = company or "Government of PEI"
    return {
        "url": str(item.get("link") or "").strip(),
        "title": re.sub(r"\s+", " ", title),
        "company": company,
        "location": "Prince Edward Island",
        "posted": str(item.get("date") or "")[:10],
        "source": "workpei",
    }


def scan_workpei_source(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="workpei", ok=True))
    max_pages = max(1, int(config.get("max_pages", 6)))
    delay = float(config.get("delay_s", 1.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            timeout=float(config.get("timeout_s", 30.0)),
            follow_redirects=True,
        )

    postings: list[JobPosting] = []
    seen: set[str] = set()
    errors = 0
    advertised: int | None = None
    total_pages = max_pages
    # Items the API returned, filled postings included — the number the
    # advertised X-WP-Total is checked against.
    listed = 0
    try:
        for page in range(1, max_pages + 1):
            if page > total_pages:
                break
            if page > 1 and delay > 0:
                sleep(delay)
            try:
                response = client.get(API, params={"per_page": 100, "page": page, "_fields": _FIELDS})
                response.raise_for_status()
                items = response.json()
            except (httpx.HTTPError, ValueError):
                errors += 1
                break
            if page == 1:
                advertised = int(response.headers.get("X-WP-Total") or 0) or None
                total_pages = int(response.headers.get("X-WP-TotalPages") or max_pages)
            # A 200 whose body is not the list (an error object, a maintenance
            # page) is a failed read, not an empty board.
            if not isinstance(items, list):
                errors += 1
                break
            if not items:
                break
            listed += len(items)
            for item in items:
                row = parse_listing(item) if isinstance(item, dict) else None
                posting = from_row(row, source_id="workpei", portal="workpei") if row else None
                if posting is None or posting.url in seen:
                    continue
                seen.add(posting.url)
                postings.append(posting)
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="workpei",
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised,
            truncated=total_pages > max_pages,
            errors=errors,
            # Ended on an empty page, or a failed one, before the advertised
            # count — only meaningful when the page budget was not the cause.
            incomplete=bool(advertised) and total_pages <= max_pages and listed < advertised,
        ),
    )
