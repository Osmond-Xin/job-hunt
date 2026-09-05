"""Direct Job Bank (jobbank.gc.ca) search adapter — no WebSearch, no quota.

Why this exists
---------------
Job Bank was originally reached through the Brave WebSearch discovery
channels with ``site:jobbank.gc.ca``. Measured on a full national scan that
returned 294 jobbank hits, only **10** were real postings: 241 were
``/marketreport/`` occupation-and-wage pages, 22 were search-result pages and
17 were outlook reports. Roughly half the scan's search quota bought a 3%
precision channel, on what is the highest-value board for an immigration-led
search (it is the federal board, and employers advertising for LMIA/PR
purposes are required to post there).

Fetching the board's own search directly costs nothing, returns only real
postings, and carries structured employer / location / salary fields.

Query parameters (verified 2026-08-06 against the live site)
------------------------------------------------------------
- ``fn21``  NOC 2021 unit-group code. This is the *working* keyword filter.
- ``fprov`` two-letter province/territory code. Verified filtering.
- ``sort=D`` newest first.

Two parameters that look right and are silently ignored — do not reintroduce
them:
- ``searchstring`` / ``locationstring``: accepted and dropped. Four different
  ``locationstring`` values returned byte-identical result sets.
- ``term``: also dropped once ``fn21`` is in play — ``term=developer``,
  ``term=analyst`` and ``term=cook`` all returned the same 25 rows. The site
  resolves a typed keyword to a NOC code server-side via session state, so
  only the resolved code travels in the URL. Target NOC codes directly.

The board is slow and will time out under rapid requests, hence the generous
default timeout and the pause between pages.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

_BASE = "https://www.jobbank.gc.ca"
_SEARCH_URL = f"{_BASE}/jobsearch/jobsearch"
_USER_AGENT = "Mozilla/5.0 (compatible; job-hunt/0.1; personal job search)"

_ARTICLE_RE = re.compile(r'<article\b[^>]*\bid="article-(\d+)"(.*?)</article>', re.S)
_HREF_RE = re.compile(r'href="(/jobsearch/jobposting/[^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_LI_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _li(field: str) -> re.Pattern[str]:
    """Compiled matcher for ``<li class="field"> ... </li>``."""
    if field not in _LI_RE_CACHE:
        # No \b after the quote: `"` and `>` are both non-word characters, so
        # a word boundary there never matches and every field came back empty.
        _LI_RE_CACHE[field] = re.compile(rf'<li class="{field}"[^>]*>(.*?)</li>', re.S)
    return _LI_RE_CACHE[field]


def _text(fragment: str) -> str:
    """Strip tags and collapse whitespace.

    Job Bank nests markup inside the very fields we want — the location cell
    carries a screen-reader ``<span class="wb-inv">Location</span>`` label and
    sometimes a tooltip ``<span class="description">``, and some titles embed
    a recruiter badge. Tag-stripping is required, not cosmetic.
    """
    without_tooltip = re.sub(
        r'<span class="description".*?</span>\s*</span>', " ", fragment, flags=re.S
    )
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", without_tooltip))).strip()


def _clean_posting_url(href: str) -> str:
    """Absolute posting URL with the session id and tracking query removed."""
    path = href.split("?", 1)[0]
    path = re.sub(r";jsessionid=[^?/]*", "", path, flags=re.I)
    return urljoin(_BASE, path)


def parse_jobbank_results(page_html: str) -> list[dict[str, str]]:
    """Extract posting rows from a Job Bank search-results page."""
    rows: list[dict[str, str]] = []
    for posting_id, block in _ARTICLE_RE.findall(page_html or ""):
        href = _HREF_RE.search(block)
        if not href:
            continue
        title_match = re.search(r'<span class="noctitle">(.*?)</span>', block, re.S)
        # The title span can contain a nested badge span, so the lazy match
        # above may stop early; re-read from the opening tag when it did.
        title = _text(title_match.group(1)) if title_match else ""
        if not title:
            continue
        location = _location(block)
        rows.append(
            {
                "id": posting_id,
                "url": _clean_posting_url(href.group(1)),
                "title": title,
                "company": _field(block, "business"),
                "location": location,
                "salary": _field(block, "salary").removeprefix("Salary").strip(),
                "date": _field(block, "date"),
            }
        )
    return rows


def _field(block: str, name: str) -> str:
    match = _li(name).search(block)
    return _text(match.group(1)) if match else ""


def _location(block: str) -> str:
    """Location cell minus its screen-reader label and relocation tooltip."""
    raw = _field(block, "location")
    raw = raw.removeprefix("Location").strip()
    # "Undetermined location" rows carry a long explanatory sentence.
    if raw.lower().startswith("undetermined location"):
        return "Undetermined location"
    return raw


def fetch_jobbank_page(
    noc_code: str,
    province: str | None = None,
    *,
    client: httpx.Client,
) -> str:
    """Fetch one search-results page. Returns "" on any transport failure."""
    params: dict[str, str] = {"fn21": str(noc_code), "sort": "D"}
    if province:
        params["fprov"] = province
    try:
        response = client.get(_SEARCH_URL, params=params)
    except httpx.HTTPError:
        return ""
    if response.status_code != 200:
        return ""
    return response.text


def scan_jobbank(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep=time.sleep,
    stats: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Fetch postings for every ``noc_codes`` x ``provinces`` pair.

    Returns de-duplicated rows (a posting can surface under more than one
    code). Disabled or empty config yields ``[]``.

    Pass ``stats`` to find out whether the sweep actually completed.
    ``fetch_jobbank_page`` returns ``""`` on any transport error or
    non-200 response — indistinguishable, until counted here, from a
    NOC code that genuinely has zero postings in a province. This is the
    highest-volume tier (9 NOC codes x 13 provinces = 117 requests per
    run), so a quiet failure here is the biggest undercount risk of the
    five. Keyed by NOC code, same as ``row["noc"]`` below.
    """
    if not config or not config.get("enabled", False):
        return []
    codes = [str(c).strip() for c in (config.get("noc_codes") or []) if str(c).strip()]
    provinces = [
        str(p).strip() for p in (config.get("provinces") or []) if str(p).strip()
    ]
    if not codes:
        return []
    # No province list means one national query per code.
    targets: list[str | None] = list(provinces) if provinces else [None]
    delay = float(config.get("delay_s", 2.0))
    timeout = float(config.get("timeout_s", 45.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
    try:
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        first = True
        for code in codes:
            entry = stats.setdefault(code, {"collected": 0, "errors": 0}) if stats is not None else None
            for province in targets:
                if not first and delay > 0:
                    sleep(delay)
                first = False
                page = fetch_jobbank_page(code, province, client=client)
                if page == "" and entry is not None:
                    entry["errors"] += 1
                for row in parse_jobbank_results(page):
                    if row["url"] in seen:
                        continue
                    seen.add(row["url"])
                    row["noc"] = code
                    out.append(row)
                    if entry is not None:
                        entry["collected"] += 1
        return out
    finally:
        if owns_client:
            client.close()
