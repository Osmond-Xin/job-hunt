"""Tier 11: single-employer career sites on Oracle, Jibe, UKG, hrSmart, SuccessFactors and eRecruit.

Each of these platforms serves every one of its customers through the same
public JSON endpoint, so an employer is one ``portals.yml`` entry — a
``platform`` and the URL of its career site — rather than code:

- **oracle** — Oracle Recruiting Cloud; see ``services/oracle_recruiting.py``.
  J.D. Irving (``hcpd.fa.ca2`` / ``CX_5003``): 736 postings on 2026-09-27
  across its divisions — Cavendish Farms, Kent, Midland, the pulp mills.
- **jibe** — iCIMS's hosted career site (Jibe). ``GET <site>/api/jobs?page=N
  &limit=100`` -> ``{"totalCount": N, "jobs": [{"data": {slug, title, city,
  state, country, posted_date}}]}``. City of Thunder Bay: 32 postings.
- **ukg** — UKG Pro Recruiting (formerly UltiPro). ``POST <board>/JobBoardView/
  LoadSearchResults`` -> ``{"totalCount": N, "opportunities": [{Id, Title,
  Locations, PostedDate}]}``. The board URL carries the tenant code and a
  GUID; neither can be guessed — Yukon Hospitals' root ``/YUK5001YUHC/
  JobBoard/`` is a 404, and the GUID came from the redirect behind its own
  careers page. 52 postings.
- **hrsmart** — hrSmart (``*.hua.hrsmart.com``), plain server-rendered HTML:
  ``<site>/hr/ats/JobSearch/viewAll`` then ``.../viewAll/
  jobSearchPaginationExternal_page:N``, 25 rows a page, no advertised total;
  a page past the end repeats the last one. Column order differs per tenant,
  so a row is read as its ``/hr/ats/Posting/view/<id>`` link plus whichever
  cell holds a ``City, PR, CA`` address. Government of Yukon (47 postings,
  mostly nurses and trades) and BC Public Service (84, 4 pages).
- **erecruit** — the eRecruit RSS feed (``<site>/RSS.aspx``) Ontario
  hospitals run. It is not well-formed XML — each posting is an ``<h1>``
  title followed by its ``<description>``, ``<link>`` and ``<pubDate>`` — so
  it is read as that sequence rather than parsed as RSS. One feed, no paging.
  Niagara Health: 36 postings on 2026-09-27, locations like "NIAGARA FALLS
  SITE", hence ``location_hint``.
- **successfactors** — SAP SuccessFactors career sites, read with the parser
  the public-sector boards use (``gov_boards.parse_successfactors``); the
  total is the page's "of <b>N</b>". For crown corporations — ICBC,
  SaskPower, SaskTel — which are general employers and so keep the title
  filter; the public-service SuccessFactors boards stay in ``gov_boards``.

These are general employers, not occupation-scoped boards: J.D. Irving's
first 200 postings on 2026-09-27 were drivers, mechanics, store managers and
mill students, and Yukon Hospitals posts laboratory and nursing roles. So
scan.py runs this tier through the **positive** title filter, like Getro
(tier 9), and a discarded analyst-shaped title still reaches the near-miss
ledger that ``triage --second-look`` reads.
"""

from __future__ import annotations

import html
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.gov_boards import parse_successfactors
from job_hunt.services.oracle_recruiting import (
    MAX_PAGE_SIZE,
    fetch_requisitions,
    parse_requisitions,
    requisitions_total,
    resolve_oracle_site,
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
_PAGE_SIZE = 100
_UKG_BOARD_RE = re.compile(r"^(https://[^/]+/[^/]+/JobBoard/[0-9a-fA-F-]{36})")

# A page reader returns (rows, advertised total), or None when the request failed.
Page = tuple[list[dict[str, str]], int] | None


def _oracle_reader(employer: dict[str, Any]):
    target = resolve_oracle_site(str(employer.get("url") or ""))
    if target is None:
        return None
    base, site = target

    def read(client: httpx.Client, page: int) -> Page:
        payload = fetch_requisitions(
            client, base, site, offset=page * MAX_PAGE_SIZE, limit=MAX_PAGE_SIZE
        )
        if payload is None:
            return None
        rows = parse_requisitions(payload, base=base, site=site, company="")
        return rows, requisitions_total(payload)

    return read


def _jibe_reader(employer: dict[str, Any]):
    parsed = urlparse(str(employer.get("url") or ""))
    if not parsed.netloc:
        return None
    base = f"{parsed.scheme or 'https'}://{parsed.netloc}"

    def read(client: httpx.Client, page: int) -> Page:
        try:
            response = client.get(f"{base}/api/jobs", params={"page": page + 1, "limit": _PAGE_SIZE})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        # A 200 without the list is a changed API, not a board with no jobs.
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return None
        rows = []
        for job in payload["jobs"]:
            data = job.get("data") or {}
            slug = str(data.get("slug") or data.get("req_id") or "").strip()
            if not slug:
                continue
            rows.append(
                {
                    "url": f"{base}/jobs/{slug}",
                    "title": str(data.get("title") or "").strip(),
                    "location": ", ".join(
                        str(data[k]) for k in ("city", "state", "country") if data.get(k)
                    ),
                    "posted": str(data.get("posted_date") or "")[:10],
                }
            )
        return rows, int(payload.get("totalCount") or 0)

    return read


def _ukg_location(locations: list[dict[str, Any]]) -> str:
    """The Canadian location when the posting lists one, else the first.

    A posting may list several; keeping only the first dropped a
    Boston-and-Toronto posting at the Canada gate.
    """
    addresses = [(loc or {}).get("Address") or {} for loc in locations]
    canadian = [
        a for a in addresses
        if str((a.get("Country") or {}).get("Code") or "").upper() in ("CAN", "CA")
        or str((a.get("Country") or {}).get("Name") or "").strip().lower() == "canada"
    ]
    address = (canadian or addresses or [{}])[0]
    place = [address.get("City"), (address.get("State") or {}).get("Name"),
             (address.get("Country") or {}).get("Name")]
    return ", ".join(str(part) for part in place if part)


def _ukg_reader(employer: dict[str, Any]):
    match = _UKG_BOARD_RE.match(str(employer.get("url") or ""))
    if not match:
        return None
    board = match.group(1)

    def read(client: httpx.Client, page: int) -> Page:
        body = {
            "opportunitySearch": {
                "Top": _PAGE_SIZE,
                "Skip": page * _PAGE_SIZE,
                "QueryString": "",
                "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
                "Filters": [],
            },
            "matchCriteria": {
                "PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [],
                "Skills": [], "hasNoLicenses": False, "SkippedSkills": [],
            },
        }
        try:
            response = client.post(f"{board}/JobBoardView/LoadSearchResults", json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("opportunities"), list):
            return None
        rows = []
        for item in payload["opportunities"]:
            job_id = str(item.get("Id") or "").strip()
            if not job_id:
                continue
            rows.append(
                {
                    "url": f"{board}/OpportunityDetail?opportunityId={job_id}",
                    "title": str(item.get("Title") or "").strip(),
                    "location": _ukg_location(item.get("Locations") or []),
                    "posted": str(item.get("PostedDate") or "")[:10],
                }
            )
        return rows, int(payload.get("totalCount") or 0)

    return read


_HRSMART_ROW_RE = re.compile(r"<tr\b(.*?)</tr>", re.S)
_HRSMART_LINK_RE = re.compile(r'href="/hr/ats/Posting/view/(\d+)"[^>]*>(.*?)</a>', re.S)
_HRSMART_PLACE_RE = re.compile(r"<td[^>]*>\s*([^<]+?,\s*[A-Z]{2}),\s*CA\b")
_TAG_RE = re.compile(r"<[^>]+>")


def parse_hrsmart(page_html: str, site: str) -> list[dict[str, str]]:
    rows = []
    for block in _HRSMART_ROW_RE.findall(page_html or ""):
        link = _HRSMART_LINK_RE.search(block)
        if not link:
            continue
        place = _HRSMART_PLACE_RE.search(block)
        rows.append(
            {
                "url": f"{site}/hr/ats/Posting/view/{link.group(1)}",
                "title": re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", link.group(2)))).strip(),
                "location": f"{html.unescape(place.group(1)).strip()}, Canada" if place else "",
            }
        )
    return rows


def _hrsmart_reader(employer: dict[str, Any]):
    parsed = urlparse(str(employer.get("url") or ""))
    if not parsed.netloc.endswith(".hrsmart.com"):
        return None
    site = f"https://{parsed.netloc}"

    def read(client: httpx.Client, page: int) -> Page:
        path = "/hr/ats/JobSearch/viewAll"
        if page:
            path += f"/jobSearchPaginationExternal_page:{page + 1}"
        try:
            response = client.get(site + path)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        rows = parse_hrsmart(response.text, site)
        # A first page with no rows is markup the parser no longer reads.
        return (rows, 0) if rows or page else None

    return read


_SF_TOTAL_RE = re.compile(r"of\s*<b>\s*([\d,]+)\s*</b>")


def _successfactors_reader(employer: dict[str, Any]):
    parsed = urlparse(str(employer.get("url") or ""))
    if not parsed.netloc:
        return None
    base = f"https://{parsed.netloc}"
    name = str(employer.get("name") or "")

    def read(client: httpx.Client, page: int) -> Page:
        try:
            response = client.get(f"{base}/search/", params={"q": "", "startrow": page * 25})
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        rows = parse_successfactors(response.text, base=base, company=name)
        total = _SF_TOTAL_RE.search(response.text)
        # A first page with neither rows nor a result count is markup the
        # parser no longer reads, not an employer with no openings.
        if page == 0 and not rows and not total:
            return None
        return rows, int(total.group(1).replace(",", "")) if total else 0

    return read


_ERECRUIT_ITEM_RE = re.compile(r"<h1>(.*?)</h1>(.*?)(?=<h1>|$)", re.S)


def parse_erecruit(feed: str) -> list[dict[str, str]]:
    rows = []
    for title, body in _ERECRUIT_ITEM_RE.findall(feed or ""):
        link = re.search(r"<link>\s*(\S+?)\s*</link>", body)
        if not link:
            continue
        place = re.search(r"LOCATION:\s*([^<]*)<", body)
        posted = re.search(r"<pubDate>\s*(.*?)\s*</pubDate>", body)
        try:
            date = parsedate_to_datetime(posted.group(1)).date().isoformat() if posted else ""
        except (TypeError, ValueError):
            date = ""
        rows.append(
            {
                "url": html.unescape(link.group(1)),
                "title": re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", title))).strip(),
                "location": html.unescape(place.group(1)).strip() if place else "",
                "posted": date,
            }
        )
    return rows


def _erecruit_reader(employer: dict[str, Any]):
    parsed = urlparse(str(employer.get("url") or ""))
    if not parsed.netloc:
        return None
    feed_url = f"https://{parsed.netloc}{parsed.path.rstrip('/')}/RSS.aspx"

    def read(client: httpx.Client, page: int) -> Page:
        if page:
            return [], 0  # one feed, no paging
        try:
            response = client.get(feed_url)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        rows = parse_erecruit(response.text)
        # A hospital always has postings; none parsed is a changed feed.
        return (rows, len(rows)) if rows else None

    return read


_READERS = {
    "oracle": _oracle_reader,
    "jibe": _jibe_reader,
    "ukg": _ukg_reader,
    "hrsmart": _hrsmart_reader,
    "successfactors": _successfactors_reader,
    "erecruit": _erecruit_reader,
}


def scan_employer_boards_source(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    """Every configured employer's whole board, as one source.

    Each board is walked until its advertised total is reached, a page brings
    nothing new, or ``max_pages`` runs out — the last is ``truncated``, and
    ending below the advertised total any other way is ``incomplete``. An
    entry whose platform or URL cannot be read counts as an error, so a typo
    in portals.yml is not mistaken for an employer with no openings.
    """
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="employer_boards", ok=True))

    employers = [e for e in (config.get("employers") or []) if isinstance(e, dict) and e.get("enabled", True)]
    max_pages = max(1, int(config.get("max_pages", 10)))
    delay = float(config.get("delay_s", 1.0))
    timeout = float(config.get("timeout_s", 30.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=timeout, follow_redirects=True)

    postings: list[JobPosting] = []
    seen: set[str] = set()
    errors = 0
    advertised = 0
    truncated = False
    short: list[str] = []
    first = True
    try:
        for employer in employers:
            make_reader = _READERS.get(str(employer.get("platform") or "").lower())
            read = make_reader(employer) if make_reader else None
            if read is None:
                errors += 1
                continue
            name = str(employer.get("name") or "").strip()
            # For boards that name a site, not a town ("NIAGARA FALLS SITE").
            hint = str(employer.get("location_hint") or "").strip()
            board_urls: set[str] = set()
            total = 0
            failed = False
            # Per-employer budget, as in workday_boards: Island Health is ~50
            # hrSmart pages where J.D. Irving is 4 Oracle pages.
            for page in range(max(1, int(employer.get("max_pages", max_pages)))):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                got = read(client, page)
                if got is None:
                    errors += 1
                    failed = True
                    break
                rows, page_total = got
                # The first total a board gives is kept: a later page that
                # omits it must not erase what is known.
                total = total or page_total
                fresh = [row for row in rows if row["url"] not in board_urls]
                if not fresh:
                    break
                for row in fresh:
                    board_urls.add(row["url"])
                    if row["url"] in seen:
                        continue
                    if hint:
                        row["location"] = f"{row['location']}, {hint}" if row.get("location") else hint
                    posting = from_row(
                        {**row, "company": name, "source": f"employer_boards:{name}"},
                        source_id="employer_boards",
                        portal=str(employer.get("platform")).lower(),
                    )
                    if posting is not None:
                        seen.add(row["url"])
                        postings.append(posting)
                if total and len(board_urls) >= total:
                    break
            else:
                truncated = True
                advertised += total
                continue
            # Ended on an empty or repeated page below the board's own count:
            # rows were missed. A failed read is already an error.
            if total and not failed and len(board_urls) < total:
                short.append(f"{name} {len(board_urls)} of {total}")
            advertised += total
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="employer_boards",
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised or None,
            truncated=truncated,
            errors=errors,
            incomplete=bool(short),
            # Per employer: a sum across boards mixes ones that advertise a
            # total with ones (hrSmart) that do not.
            incomplete_detail="; ".join(short),
        ),
    )
