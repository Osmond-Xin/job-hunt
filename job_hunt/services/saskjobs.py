"""Tier 4b: SaskJobs.ca, the Government of Saskatchewan's job board.

Saskatchewan runs an SINP stream for in-province job offers, and SaskJobs is
where the province's own employers post — small ones included, many of them
never reaching the national aggregators. The listing is plain server-rendered
JSP, one table row per posting, and every row carries its NOC 2021 code::

    GET https://www.saskjobs.ca/jsp/joborder/listing.jsp
        ?filter_by=2&noc_skill_type=<d>&catsort=0&page=<n>

``noc_skill_type`` is the NOC **broad category** — the first digit of the code
(measured 2026-09-27: type 1 returned only ``NOC 1xxxx`` rows over 6 pages,
type 2 only ``NOC 2xxxx`` rows over 2). ``catsort=0`` is "all jobs", not a
recency window. 100 rows a page; a page past the end comes back empty.

So the tier needs no keyword guessing: it walks each broad category that the
configured NOC codes fall in and keeps the rows whose code is on the list —
the same list Job Bank (tier 4) is queried with, so the two cannot drift.
Occupation is established by the code, which is why scan.py waives the
positive title filter here as it does for Job Bank.

Some rows are Job Bank mirrors (a Job Bank icon in the last cell). They are
kept: Job Bank is only swept for the configured NOC x province pairs, and a
mirror under a different title is still one more chance to see the posting.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import time
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row

LISTING = "https://www.saskjobs.ca/jsp/joborder/listing.jsp"
DETAIL = "https://www.saskjobs.ca/jsp/joborder/detail.jsp?job_order_id={id}"
_USER_AGENT = "Mozilla/5.0 (compatible; job-hunt/0.1; personal job search)"

_ROW_RE = re.compile(
    r'<a href="/jsp/joborder/detail\.jsp\?job_order_id=(?P<id>\d+)">(?P<title>.*?)</a>\s*<br\s*/?>\s*'
    r'<span[^>]*>\s*NOC\s*(?P<noc>\d{5})\s*</span>\s*</td>\s*'
    r"<td[^>]*>(?P<employer>.*?)</td>\s*"
    r"<td[^>]*>(?P<city>.*?)</td>\s*"
    r"<td[^>]*>(?P<date>.*?)</td>",
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def _iso(value: str) -> str:
    """``27-Sep-2026`` -> ``2026-09-27``; "" when it is not that shape."""
    try:
        return dt.datetime.strptime(value, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def parse_saskjobs(page_html: str) -> list[dict[str, str]]:
    """Every posting row on one listing page."""
    rows: list[dict[str, str]] = []
    for match in _ROW_RE.finditer(page_html or ""):
        city = _text(match.group("city")).title()
        rows.append(
            {
                "url": DETAIL.format(id=match.group("id")),
                "title": _text(match.group("title")),
                "company": _text(match.group("employer")),
                # The board prints the town alone, in capitals ("WEYBURN");
                # naming the province is what lets the Canada gate place it.
                "location": f"{city}, Saskatchewan" if city else "Saskatchewan",
                "noc": match.group("noc"),
                "posted": _iso(_text(match.group("date"))),
            }
        )
    return rows


def scan_saskjobs_source(
    config: dict[str, Any] | None,
    noc_codes: list[str] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    """Postings whose NOC code is on ``noc_codes``, across every page."""
    codes = {str(code).strip() for code in (noc_codes or []) if str(code).strip()}
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="saskjobs", ok=True))
    if not codes:
        # Enabled but with nothing to filter by: say so, or it reads as a
        # province with no openings.
        return SourceResult(
            postings=[],
            health=SourceHealth(
                source_id="saskjobs", ok=False,
                note="enabled, but jobbank_direct.noc_codes is empty — nothing read",
            ),
        )
    categories = sorted({code[0] for code in codes})
    max_pages = max(1, int(config.get("max_pages", 10)))
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
    truncated = False
    first = True
    try:
        for category in categories:
            for page in range(1, max_pages + 1):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                try:
                    response = client.get(
                        LISTING,
                        params={"filter_by": 2, "noc_skill_type": category, "catsort": 0, "page": page},
                    )
                    response.raise_for_status()
                except httpx.HTTPError:
                    errors += 1
                    break
                parsed = parse_saskjobs(response.text)
                # Every category holds postings; a first page that parses to
                # nothing is markup the regex no longer matches, not a quiet week.
                if page == 1 and not parsed:
                    errors += 1
                rows = [row for row in parsed if row["url"] not in seen]
                if not rows:
                    break
                for row in rows:
                    seen.add(row["url"])
                    if row["noc"] not in codes:
                        continue
                    posting = from_row(
                        {**row, "source": f"saskjobs NOC {row['noc']}"},
                        source_id="saskjobs",
                        portal="saskjobs",
                    )
                    if posting is not None:
                        postings.append(posting)
            else:
                truncated = True
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="saskjobs",
            ok=errors == 0,
            collected=len(postings),
            truncated=truncated,
            errors=errors,
        ),
    )
