"""Tier 17: Built In Vancouver (builtinvancouver.org), a tech-company job board.

Server-rendered HTML: ``/jobs?search=<role>&page=N`` returns 25 cards a
page, each ``data-id="job-card"`` holding the company (``company-title``),
the title and link (``job-card-title``), a posted age ("2 Days Ago") and a
location after the ``fa-location-dot`` icon ("Richmond, BC, CAN"). Measured
2026-09-27. Some cards say only "CA" or "Remote" — whether that is Canada or
California is the Canada filter's call, and it resolves doubt towards reading
the posting.

No result count is published: a page with fewer than 25 cards ends a search,
and every search coming back with no cards at all is reported as an error —
the markup changed, not the market. A general board, searched by the
profile's role terms, so the positive title filter applies.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.posted_date import relative_age_to_iso

BASE = "https://www.builtinvancouver.org"
_PAGE_SIZE = 25
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
_TITLE_RE = re.compile(r'<a href="([^"]+)"[^>]*data-id="job-card-title"[^>]*>(.*?)</a>', re.S)
_COMPANY_RE = re.compile(r'data-id="company-title"[^>]*>\s*(?:<span>)?(.*?)<', re.S)
_LOCATION_RE = re.compile(r"fa-location-dot[^>]*></i>.*?<span[^>]*>([^<]*)</span>", re.S)
_AGE_RE = re.compile(r"(\d+\s+\w+\s+Ago|Today|Yesterday)", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_cards(page_html: str) -> list[dict[str, str]]:
    rows = []
    for card in (page_html or "").split('data-id="job-card"')[1:]:
        title = _TITLE_RE.search(card)
        if not title:
            continue
        company = _COMPANY_RE.search(card)
        place = _LOCATION_RE.search(card)
        age = _AGE_RE.search(card)
        rows.append(
            {
                "url": urljoin(BASE, html.unescape(title.group(1))),
                "title": html.unescape(_TAG_RE.sub(" ", title.group(2))).strip(),
                "company": html.unescape(company.group(1)).strip() if company else "",
                "location": html.unescape(place.group(1)).strip() if place else "",
                "posted": relative_age_to_iso(age.group(1)) if age else "",
                "source": "builtin_vancouver",
            }
        )
    return rows


def scan_builtin_source(
    config: dict[str, Any] | None,
    roles: list[str],
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="builtin_vancouver", ok=True))
    queries = [str(r).strip() for r in (config.get("queries") or roles or []) if str(r).strip()]
    if not queries:
        return SourceResult(
            postings=[],
            health=SourceHealth(source_id="builtin_vancouver", ok=False, note="enabled, but no role terms to search"),
        )
    max_pages = max(1, int(config.get("max_pages", 2)))
    delay = float(config.get("delay_s", 1.2))

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
    truncated = False
    cards_seen = 0
    first = True
    try:
        for query in queries:
            for page in range(1, max_pages + 1):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                try:
                    response = client.get(f"{BASE}/jobs", params={"search": query, "page": page})
                    response.raise_for_status()
                except httpx.HTTPError:
                    errors += 1
                    break
                rows = parse_cards(response.text)
                cards_seen += len(rows)
                for row in rows:
                    posting = from_row(row, source_id="builtin_vancouver", portal="builtin")
                    if posting is None or posting.url in seen:
                        continue
                    seen.add(posting.url)
                    postings.append(posting)
                if len(rows) < _PAGE_SIZE:
                    break
            else:
                truncated = True
    finally:
        if owns_client:
            client.close()

    note = ""
    if not cards_seen and not errors:
        errors, note = 1, "no job cards on any search page — the markup may have changed"
    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="builtin_vancouver",
            ok=errors == 0,
            collected=len(postings),
            truncated=truncated,
            errors=errors,
            note=note,
        ),
    )
