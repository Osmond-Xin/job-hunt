"""Tier 17: Built In Vancouver (builtinvancouver.org), a tech-company job board.

Server-rendered HTML: ``/jobs?search=<role>&page=N`` returns 25 cards a
page, each ``data-id="job-card"`` holding the company (``company-title``),
the title and link (``job-card-title``), a posted age ("2 Days Ago") and a
location after the ``fa-location-dot`` icon ("Richmond, BC, CAN"). Measured
2026-09-27. Some cards say only "CA" or "Remote" — whether that is Canada or
California is the Canada filter's call, and it resolves doubt towards reading
the posting.

No result count is published. A page with fewer than 25 cards — counted raw,
before parsing — ends a search; a page with no cards is the end only when it
says "No Results Found", and is otherwise an error (a block page or changed
markup). A card that does not parse is counted as lost. A general board, searched by the
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
_CARD = 'data-id="job-card"'
_EMPTY_MARKER = "No Results Found"
_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S)
_COMPANY_RE = re.compile(r'data-id="company-title"[^>]*>\s*(?:<span[^>]*>)?(.*?)<', re.S)
_LOCATION_RE = re.compile(r"fa-location-dot[^>]*></i>.*?<span[^>]*>([^<]*)</span>", re.S)
# "2 Days Ago", "30+ Days Ago", "Reposted One Month Ago", "Today".
_AGE_RE = re.compile(r"((?:\d+\+?|One|An?)\s+\w+\s+Ago|Today|Yesterday)", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _title_link(card: str) -> tuple[str, str] | None:
    """(href, title) of the card's title link, whatever its attribute order."""
    for attrs, text in _ANCHOR_RE.findall(card):
        if 'data-id="job-card-title"' in attrs:
            href = re.search(r'href="([^"]+)"', attrs)
            return (href.group(1) if href else ""), text
    return None


def _age(card: str) -> str:
    match = _AGE_RE.search(card)
    if not match:
        return ""
    return relative_age_to_iso(re.sub(r"\b(?:one|an?)\b", "1", match.group(1), flags=re.I))


def parse_cards(page_html: str) -> list[dict[str, str]]:
    rows = []
    for card in (page_html or "").split(_CARD)[1:]:
        title = _title_link(card)
        if not title or not title[0]:
            continue
        company = _COMPANY_RE.search(card)
        place = _LOCATION_RE.search(card)
        rows.append(
            {
                "url": urljoin(BASE, html.unescape(title[0])),
                "title": html.unescape(_TAG_RE.sub(" ", title[1])).strip(),
                "company": html.unescape(company.group(1)).strip() if company else "",
                "location": html.unescape(place.group(1)).strip() if place else "",
                "posted": _age(card),
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
    lost = 0
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
                raw = response.text.count(_CARD)
                if not raw and _EMPTY_MARKER not in response.text:
                    errors += 1  # a block page or changed markup, not the end
                    break
                rows = parse_cards(response.text)
                lost += raw - len(rows)
                for row in rows:
                    posting = from_row(row, source_id="builtin_vancouver", portal="builtin")
                    if posting is None or posting.url in seen:
                        continue
                    seen.add(posting.url)
                    postings.append(posting)
                if raw < _PAGE_SIZE:
                    break
            else:
                truncated = True
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="builtin_vancouver",
            ok=errors == 0,
            collected=len(postings),
            truncated=truncated,
            errors=errors,
            incomplete=bool(lost),
            incomplete_detail=f"{lost} job card(s) could not be parsed" if lost else "",
        ),
    )
