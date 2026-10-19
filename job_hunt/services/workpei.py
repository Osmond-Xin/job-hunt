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
_TAG_RE = re.compile(r"<[^>]+>")


def _is_filled(value: Any) -> bool:
    """``_filled`` is the integer 0 or 1 in the live payload; a WordPress
    plugin can serialise it as "0" / "1" instead, and ``bool("0")`` is True."""
    return str(value).strip().lower() in {"1", "true"}


def _well_formed(item: Any) -> bool:
    """The fields read below have the types the API sends, or are absent.
    One malformed item is counted as lost; it must not raise and discard the
    whole sweep."""
    return (
        isinstance(item, dict)
        and isinstance(item.get("meta") or {}, dict)
        and isinstance(item.get("title") or {}, dict)
        and isinstance(item.get("link") or "", str)
        and isinstance(item.get("class_list") or [], list)
    )


def parse_listing(item: dict[str, Any]) -> dict[str, str] | None:
    """A pipeline row from one REST item, or None for a filled posting."""
    meta = item.get("meta") or {}
    if _is_filled(meta.get("_filled")):
        return None
    rendered = str((item.get("title") or {}).get("rendered") or "")
    title = html.unescape(_TAG_RE.sub(" ", rendered)).strip()
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
    # None until the API says; with no header the end is unknown, so a walk
    # that uses the whole budget is truncated, never "done".
    total_pages: int | None = None
    # Unique item ids the API returned, filled postings included — the number
    # the advertised X-WP-Total is checked against.
    listed: set[int | str] = set()
    # Filled items with no id: skipped on purpose, but still part of the total.
    filled_without_id = 0
    # An empty page at or before the last advertised page.
    empty_early = False
    # Items that were not usable rows (null, no id, no title or link) or that
    # repeated an id already read. Neither is a quiet board.
    lost = 0
    # How the walk ended: "done" (past the last advertised page), "budget"
    # (max_pages ran out first), "empty" (an empty page before the last
    # advertised one) or "error" (already counted in `errors`).
    ended = "budget"
    try:
        for page in range(1, max_pages + 1):
            if total_pages is not None and page > total_pages:
                ended = "done"
                break
            if page > 1 and delay > 0:
                sleep(delay)
            try:
                response = client.get(API, params={"per_page": 100, "page": page, "_fields": _FIELDS})
                response.raise_for_status()
                items = response.json()
            except (httpx.HTTPError, ValueError):
                errors += 1
                ended = "error"
                break
            if page == 1:
                count = response.headers.get("X-WP-Total") or ""
                advertised = int(count) if count.isdigit() and int(count) else None
                header = response.headers.get("X-WP-TotalPages")
                total_pages = int(header) if header and header.isdigit() else None
            # A 200 whose body is not the list (an error object, a maintenance
            # page) is a failed read, not an empty board.
            if not isinstance(items, list):
                errors += 1
                ended = "error"
                break
            if not items:
                ended = "empty"
                empty_early = total_pages is not None and page <= total_pages
                break
            for item in items:
                if not _well_formed(item):
                    lost += 1
                    continue
                item_id = item.get("id")
                usable_id = isinstance(item_id, (int, str)) and not isinstance(item_id, bool)
                # A repeated id is lost whether or not the item is filled.
                if usable_id and item_id in listed:
                    lost += 1
                    continue
                # A filled item is skipped on purpose, with or without an id.
                if _is_filled((item.get("meta") or {}).get("_filled")):
                    if usable_id:
                        listed.add(item_id)
                    else:
                        filled_without_id += 1
                    continue
                if not usable_id:
                    lost += 1
                    continue
                listed.add(item_id)
                row = parse_listing(item)
                posting = from_row(row, source_id="workpei", portal="workpei") if row else None
                if posting is None:
                    lost += 1
                    continue
                if posting.url in seen:
                    continue
                seen.add(posting.url)
                postings.append(posting)
        else:
            if total_pages is not None and total_pages <= max_pages:
                ended = "done"
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
            # Only a walk the page budget actually stopped is truncated.
            truncated=ended == "budget",
            errors=errors,
            # An empty page early, or a walk that read every advertised page
            # and still came up short. A failed read is already an error and
            # is not reported twice.
            incomplete=bool(lost) or empty_early or (
                ended not in ("error", "budget")
                and bool(advertised)
                and len(listed) + filled_without_id < advertised
            ),
        ),
    )
