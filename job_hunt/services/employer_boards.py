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
- **peoplesoft** — Oracle PeopleSoft Candidate Gateway (Fluid). The search
  page is a form: GET it, post its hidden fields back with
  ``ICAction=NAV_PB$0``, and the response lists every opening at once as
  ``SCH_JOB_TITLE$n`` / ``HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID$n`` /
  ``LOCATION$n`` / ``SCH_OPENED$n`` spans. ``url`` is the search page with
  its ``Siteid``. Niagara Region: 17 openings on 2026-09-27, locations named
  by facility ("Regional Headquarters"), hence ``location_hint``.
- **taleo** — Taleo classic career sections (``<host>/careersection/<n>/
  jobsearch.ftl``). The first page is embedded in the page's
  ``initialHistory`` field and later ones come from ``jobsearch.ajax`` with
  the page's hidden fields and ``rlPager.currentPage``; both are "!|!"-joined
  strings. Columns differ per tenant, so a posting is found by the pattern
  every tenant shares — the requisition id and title repeated, then the id
  three more times — and the location comes from ``location_hint``. Total:
  ``listRequisition.nbElements``. Hamilton Health Sciences: 85 postings on
  2026-09-27, read at 100 a page (``dropListSize``) in one request.
- **adp** — ADP Workforce Now's public career centre, by company id
  (``cid``, the query parameter of any ``recruitment.html`` link):
  ``GET workforcenow.adp.com/mascsr/default/careercenter/public/events/
  staffing/v1/job-requisitions?cid=<cid>&$top=20&$skip=<1-based>`` ->
  ``{jobRequisitions: [{requisitionTitle, postDate, requisitionLocations,
  customFieldGroup.stringFields[ExternalJobID]}], meta: {totalNumber}}``.
  Niagara Parks: 4 postings on 2026-09-27.
- **tbe** — Taleo Business Edition (``<host>/<zone>/ats/careers/v2/
  searchResults?org=<ORG>&cws=<n>``). One request with ``maxRows=500``
  lists every requisition as a ``viewJobLink`` anchor; no location is
  printed, hence ``location_hint``. Niagara College (6) and the City of St.
  Catharines (10) on 2026-09-27.
- **cornerstone** — Cornerstone OnDemand career sites (``<corp>.csod.com/
  ux/ats/careersite/<n>/home?c=<corp>``). The page embeds a short-lived
  bearer token in ``csod.context``; the search is then ``POST
  us.api.csod.com/rec-job-search/external/jobs`` with the site id, 25 a page,
  reporting ``totalCount``. The site id is not guessable: Mohawk College's
  site 1 exists and lists nothing, site 2 is the public one (8 postings,
  2026-09-27).
- **avanti** — Avanti HR career sites (``<tenant>.myavanti.ca/careers``).
  ``POST /careers/Job/Jobs`` returns every opening at once as JSON items
  (``Job``, ``Title``, ``Location``, ``ClosingDate``); the posting page is
  ``/careers/Job/Details/<Job>``. Locations are departments, hence
  ``location_hint``. City of Welland: 2026-09-27.
- **aspnet_jobsearch** — the ASP.NET WebForms board BC's Interior and
  Northern Health run (``<host>/JobSearch/s-/0-0-0-100-0-false-0-0``; the
  fourth segment is the page size, 100 at most — larger falls back to 10).
  Later pages are postbacks: the page's hidden fields posted back with
  ``__EVENTTARGET`` set to its "Next" link. Rows link ``/ViewJobPosting/<id>``;
  the count is "Records 1 - 100 of N". Interior Health 1,371 and Northern
  Health 626 postings on 2026-09-27.
- **selectminds** — Oracle Selectminds (Taleo's candidate site;
  ``albertahs.taleo.net`` redirects to one). ``GET <site>/jobs/search``
  redirects to a fresh ``/jobs/search/<id>``, later pages are
  ``/jobs/search/<id>/pageN``, 10 rows a page as ``job_list_<n>`` blocks. The
  page count is published (``jPaginateNumPages``), not the row count, so the
  total is taken as its lower bound, ``(pages - 1) * 10 + 1`` — enough to end
  the walk after the last page and to report one that stops early. Alberta
  Health Services: 127 pages on 2026-09-27.
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

import datetime as dt
import html
import json
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import quote, unquote, urlparse

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


_PS_HIDDEN_RE = re.compile(r"<input type='hidden' name='([^']+)' id='[^']*' value='([^']*)'")
_PS_FORM_RE = re.compile(r"<form[^>]*action='([^']+)'")


def _ps_field(page_html: str, name: str) -> dict[str, str]:
    return {
        index: html.unescape(value).strip()
        for index, value in re.findall(rf"id='{re.escape(name)}\$(\d+)'[^>]*>([^<]*)<", page_html)
    }


def parse_peoplesoft(page_html: str, search_url: str) -> list[dict[str, str]]:
    site_match = re.search(r"[?&]Siteid=(\d+)", search_url, re.I)
    site = site_match.group(1) if site_match else ""
    base = search_url.split("?", 1)[0]
    titles = _ps_field(page_html, "SCH_JOB_TITLE")
    ids = _ps_field(page_html, "HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID")
    places = _ps_field(page_html, "LOCATION")
    opened = _ps_field(page_html, "SCH_OPENED")
    rows = []
    for index, title in titles.items():
        job_id = ids.get(index, "")
        if not job_id:
            continue
        month, _, rest = opened.get(index, "").partition("/")
        day, _, year = rest.partition("/")
        rows.append(
            {
                "url": f"{base}?Page=HRS_APP_JBPST_FL&Action=U&FOCUS=Applicant"
                       f"&SiteId={site}&JobOpeningId={job_id}&PostingSeq=1",
                "title": title,
                "location": places.get(index, ""),
                "posted": f"{year}-{month}-{day}" if year and month and day else "",
            }
        )
    return rows


def _peoplesoft_reader(employer: dict[str, Any]):
    url = str(employer.get("url") or "")
    if "HRS_" not in url:
        return None

    def read(client: httpx.Client, page: int) -> Page:
        if page:
            return [], 0  # the gateway lists every opening in one response
        try:
            form_page = client.get(url)
            form_page.raise_for_status()
            form = dict(_PS_HIDDEN_RE.findall(form_page.text))
            form["ICAction"] = "NAV_PB$0"
            action = _PS_FORM_RE.search(form_page.text)
            response = client.post(action.group(1) if action else str(form_page.url), data=form)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        rows = parse_peoplesoft(response.text, url)
        # No count is published; a results grid with no rows is a changed page.
        if not rows and "HRS_AGNT_RSLT_I" not in response.text:
            return None
        return rows, len(rows)

    return read


# The default is 10 a page in an order that is not stable between requests —
# HHS paged at 10 returned 75 distinct of 85, the rest repeated. 100 a page
# reads a board that size in one request.
_TALEO_PAGE_SIZE = "100"
_TALEO_HIDDEN_RE = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"')
_TALEO_RECORD_RE = re.compile(r"!\|!(\d+)!\|!([^!]+)!\|!\1!\|!\2!\|!\1!\|!\1!\|!\1!\|!")
_TALEO_DATE_RE = re.compile(r"!\|!([A-Z][a-z]{2} \d{1,2}, \d{4})!\|!")


def parse_taleo(joined: str, base: str) -> list[dict[str, str]]:
    """Rows from a Taleo classic "!|!" list string (initialHistory or ajax)."""
    matches = list(_TALEO_RECORD_RE.finditer(joined or ""))
    rows = []
    for index, match in enumerate(matches):
        tail = joined[match.end(): matches[index + 1].start() if index + 1 < len(matches) else None]
        date = _TALEO_DATE_RE.search(tail)
        try:
            posted = dt.datetime.strptime(date.group(1), "%b %d, %Y").date().isoformat() if date else ""
        except ValueError:
            posted = ""
        rows.append(
            {
                "url": f"{base}jobdetail.ftl?job={match.group(1)}&lang=en",
                "title": html.unescape(unquote(match.group(2))).strip(),
                "location": "",
                "posted": posted,
            }
        )
    return rows


def _taleo_reader(employer: dict[str, Any]):
    url = str(employer.get("url") or "")
    if "/careersection/" not in url:
        return None
    base = url.split("?", 1)[0].rsplit("/", 1)[0] + "/"
    form: dict[str, str] = {}

    def read(client: httpx.Client, page: int) -> Page:
        try:
            if page == 0:
                response = client.get(
                    f"{base}jobsearch.ftl", params={"lang": "en", "dropListSize": _TALEO_PAGE_SIZE}
                )
                response.raise_for_status()
                form.clear()
                form.update(_TALEO_HIDDEN_RE.findall(response.text))
                joined = form.get("initialHistory", "")
            else:
                response = client.post(
                    f"{base}jobsearch.ajax",
                    data={**form, "ftlpageid": "reqListBasicPage",
                          "ftlinterfaceid": "requisitionListInterface", "ftlcompid": "rlPager",
                          "jsfCmdId": "rlPager", "ftlcompclass": "PagerComponent",
                          "ftlcallback": "ftlPager_processResponse", "ftlajaxid": "ftlx1",
                          "rlPager.currentPage": str(page + 1), "lang": "en",
                          "dropListSize": _TALEO_PAGE_SIZE},
                )
                response.raise_for_status()
                joined = response.text
        except httpx.HTTPError:
            return None
        total = form.get("listRequisition.nbElements", "")
        if page == 0 and not total.isdigit():
            return None  # not a classic career section, or its markup changed
        return parse_taleo(joined, base), int(total or 0)

    return read


_ADP_API = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_ADP_POSTING = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cid}&jobId={job}"
_ADP_PAGE_SIZE = 20


def parse_adp(payload: dict[str, Any], cid: str) -> list[dict[str, str]]:
    rows = []
    for req in payload.get("jobRequisitions") or []:
        fields = (req.get("customFieldGroup") or {}).get("stringFields") or []
        job = next((f.get("stringValue") for f in fields
                    if (f.get("nameCode") or {}).get("codeValue") == "ExternalJobID"), "")
        if not job:
            continue
        address = next(
            (loc.get("address") or {} for loc in req.get("requisitionLocations") or []
             if ((loc.get("address") or {}).get("cityName") or "").strip()),
            {},
        )
        region = (address.get("countrySubdivisionLevel1") or {}).get("codeValue") or ""
        rows.append(
            {
                "url": _ADP_POSTING.format(cid=cid, job=job),
                "title": str(req.get("requisitionTitle") or "").strip(),
                "location": ", ".join(p for p in (address.get("cityName", "").strip(), region) if p),
                "posted": str(req.get("postDate") or "")[:10],
            }
        )
    return rows


def _adp_reader(employer: dict[str, Any]):
    match = re.search(r"[?&]cid=([0-9a-fA-F-]{36})", str(employer.get("url") or ""))
    if not match:
        return None
    cid = match.group(1)

    def read(client: httpx.Client, page: int) -> Page:
        try:
            response = client.get(
                _ADP_API,
                params={"cid": cid, "lang": "en_CA", "locale": "en_CA",
                        "$top": _ADP_PAGE_SIZE, "$skip": page * _ADP_PAGE_SIZE + 1},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("jobRequisitions"), list):
            return None
        return parse_adp(payload, cid), int((payload.get("meta") or {}).get("totalNumber") or 0)

    return read


_TBE_MAX_ROWS = 500
_TBE_LINK_RE = re.compile(r'<a href="([^"]*viewRequisition\?[^"]*rid=\d+)"[^>]*class="viewJobLink"[^>]*>(.*?)</a>', re.S)


def parse_tbe(page_html: str) -> list[dict[str, str]]:
    rows, seen = [], set()
    for href, title in _TBE_LINK_RE.findall(page_html or ""):
        url = html.unescape(href)
        if url in seen:
            continue
        seen.add(url)
        rows.append({"url": url, "title": html.unescape(_TAG_RE.sub(" ", title)).strip(),
                     "location": "", "posted": ""})
    return rows


def _tbe_reader(employer: dict[str, Any]):
    url = str(employer.get("url") or "")
    match = re.match(r"(https://[^/]+/[^/]+/ats/careers/v2/)searchResults\?.*?org=([^&]+).*?cws=(\d+)", url)
    if not match:
        return None
    base, org, cws = match.groups()

    def read(client: httpx.Client, page: int) -> Page:
        if page:
            return [], 0  # maxRows lists every requisition in one response
        try:
            response = client.get(f"{base}searchResults",
                                  params={"org": org, "cws": cws, "act": "search", "maxRows": _TBE_MAX_ROWS})
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        if "oracletaleocwsv2" not in response.text:
            return None  # not the careers page, or its markup changed
        rows = parse_tbe(response.text)
        return rows, len(rows)

    return read


_CSOD_SEARCH = "https://us.api.csod.com/rec-job-search/external/jobs"
_CSOD_PAGE_SIZE = 25
_CSOD_CONTEXT_RE = re.compile(r"csod\.context=(\{.*?\});", re.S)


def parse_cornerstone(payload: dict[str, Any], site_url: str) -> list[dict[str, str]]:
    rows = []
    for req in (payload.get("data") or {}).get("requisitions") or []:
        req_id = req.get("requisitionId")
        if req_id is None:
            continue
        place = (req.get("locations") or [{}])[0] or {}
        month, _, rest = str(req.get("postingEffectiveDate") or "").partition("/")
        day, _, year = rest.partition("/")
        rows.append(
            {
                "url": site_url.format(req=req_id),
                "title": str(req.get("displayJobTitle") or "").strip(),
                "location": ", ".join(str(place[k]) for k in ("city", "state", "country") if place.get(k)),
                "posted": f"{year}-{int(month):02d}-{int(day):02d}" if year and month.isdigit() and day.isdigit() else "",
            }
        )
    return rows


def _cornerstone_reader(employer: dict[str, Any]):
    match = re.match(r"https://([^/.]+)\.csod\.com/ux/ats/careersite/(\d+)/", str(employer.get("url") or ""))
    if not match:
        return None
    corp, site = match.group(1), int(match.group(2))
    home = f"https://{corp}.csod.com/ux/ats/careersite/{site}/home"
    token: dict[str, str] = {}

    def read(client: httpx.Client, page: int) -> Page:
        try:
            if page == 0:
                landing = client.get(home, params={"c": corp})
                landing.raise_for_status()
                context = _CSOD_CONTEXT_RE.search(landing.text)
                token["value"] = json.loads(context.group(1)).get("token", "") if context else ""
            if not token.get("value"):
                return None
            response = client.post(
                _CSOD_SEARCH,
                headers={"Authorization": f"Bearer {token['value']}"},
                json={"careerSiteId": site, "careerSitePageId": site, "pageNumber": page + 1,
                      "pageSize": _CSOD_PAGE_SIZE, "cultureId": 1, "cultureName": "en-US",
                      "searchText": "", "states": [], "countryCodes": [], "cities": [],
                      "placeID": "", "radius": None, "postingsWithinDays": None,
                      "customFieldCheckboxKeys": [], "customFieldDropdowns": [],
                      "customFieldRadios": []},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("requisitions"), list):
            return None
        rows = parse_cornerstone(payload, f"{home}/requisition/{{req}}?c={corp}")
        return rows, int(data.get("totalCount") or 0)

    return read


def parse_avanti(payload: dict[str, Any], base: str) -> list[dict[str, str]]:
    rows = []
    for item in payload.get("items") or []:
        job = str(item.get("Job") or "").strip()
        if not job:
            continue
        try:
            closes = dt.datetime.strptime(str(item.get("ClosingDate") or "").strip(), "%b %d, %Y").date().isoformat()
        except ValueError:
            closes = ""
        rows.append(
            {
                "url": f"{base}/careers/Job/Details/{quote(job)}",
                "title": str(item.get("Title") or "").strip(),
                "location": str(item.get("Location") or "").strip(),
                "posted": "",
                "closes": closes,
            }
        )
    return rows


def _avanti_reader(employer: dict[str, Any]):
    parsed = urlparse(str(employer.get("url") or ""))
    if not parsed.netloc.endswith(".myavanti.ca"):
        return None
    base = f"https://{parsed.netloc}"

    def read(client: httpx.Client, page: int) -> Page:
        if page:
            return [], 0  # one response lists every opening
        try:
            response = client.post(f"{base}/careers/Job/Jobs", headers={"X-Requested-With": "XMLHttpRequest"})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return None
        rows = parse_avanti(payload, base)
        return rows, len(rows)

    return read


_ASPX_HIDDEN_RE = re.compile(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"')
_ASPX_NEXT_RE = re.compile(r"__doPostBack\(&#39;([^&]*pagListTop[^&]*)&#39;,&#39;&#39;\)\">Next<")
_ASPX_TOTAL_RE = re.compile(r"Records&nbsp;\s*\d+\s*&nbsp;-&nbsp;\s*\d+\s*&nbsp;of&nbsp;\s*(\d+)")
_ASPX_ROW_RE = re.compile(r'<tr id="rowItem".*?</tr>', re.S)


def parse_aspnet_jobsearch(page_html: str, base: str) -> list[dict[str, str]]:
    rows = []
    for block in _ASPX_ROW_RE.findall(page_html or ""):
        job = re.search(r'href="/ViewJobPosting/(\d+)', block)
        title = re.search(r'id="lblPosition"[^>]*>(.*?)</span>', block, re.S)
        place = re.search(r'data-col="Location".*?<a[^>]*>(.*?)</a>', block, re.S)
        if not job or not title:
            continue
        rows.append(
            {
                "url": f"{base}/ViewJobPosting/{job.group(1)}",
                "title": html.unescape(_TAG_RE.sub(" ", title.group(1))).strip(" *"),
                "location": html.unescape(_TAG_RE.sub(" ", place.group(1))).strip() if place else "",
                "posted": "",
            }
        )
    return rows


def _aspnet_jobsearch_reader(employer: dict[str, Any]):
    parsed = urlparse(str(employer.get("url") or ""))
    if not parsed.netloc:
        return None
    base = f"https://{parsed.netloc}"
    search = f"{base}/JobSearch/s-/0-0-0-100-0-false-0-0"
    state: dict[str, Any] = {}

    def read(client: httpx.Client, page: int) -> Page:
        try:
            if page == 0:
                response = client.get(search)
            else:
                target = state.get("next")
                if not target:
                    return [], state.get("total", 0)  # the last page had no Next link
                response = client.post(
                    search, data={**state["form"], "__EVENTTARGET": target, "__EVENTARGUMENT": ""}
                )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        total = _ASPX_TOTAL_RE.search(response.text)
        if page == 0 and not total:
            return None  # not this board, or its markup changed
        state["form"] = dict(_ASPX_HIDDEN_RE.findall(response.text))
        next_link = _ASPX_NEXT_RE.search(response.text)
        state["next"] = next_link.group(1) if next_link else ""
        state["total"] = int(total.group(1)) if total else state.get("total", 0)
        return parse_aspnet_jobsearch(response.text, base), state["total"]

    return read


_SM_ROW_RE = re.compile(r'id="job_list_\d+".*?<a href="([^"]+)" class="job_link[^"]*">(.*?)</a>'
                        r'(?:.*?<span class="location">(.*?)</span>)?', re.S)
_SM_PAGES_RE = re.compile(r'id="jPaginateNumPages"[^>]*>\s*([\d.]+)')
_SM_PAGE_SIZE = 10


def parse_selectminds(page_html: str) -> list[dict[str, str]]:
    blocks = re.split(r'(?=<div id="job_list_\d+")', page_html or "")
    rows = []
    for block in blocks:
        match = _SM_ROW_RE.search(block)
        if not match:
            continue
        rows.append(
            {
                "url": html.unescape(match.group(1)),
                "title": html.unescape(_TAG_RE.sub(" ", match.group(2))).strip(),
                "location": re.sub(r"\s+", " ", html.unescape(match.group(3) or "")).strip(),
                "posted": "",
            }
        )
    return rows


def _selectminds_reader(employer: dict[str, Any]):
    parsed = urlparse(str(employer.get("url") or ""))
    if not parsed.netloc:
        return None
    root = f"https://{parsed.netloc}"
    state: dict[str, Any] = {}

    def read(client: httpx.Client, page: int) -> Page:
        if page and page >= state.get("pages", 0):
            return [], state.get("total", 0)  # past the published page count
        try:
            if page == 0:
                response = client.get(f"{root}/jobs/search")
                state["search"] = str(response.url).rstrip("/")
            else:
                response = client.get(f"{state['search']}/page{page + 1}")
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        if page == 0:
            pages = _SM_PAGES_RE.search(response.text)
            if not pages:
                return None  # not a Selectminds search page, or its markup changed
            state["pages"] = int(float(pages.group(1)))
            state["total"] = max(0, (state["pages"] - 1) * _SM_PAGE_SIZE + 1) if state["pages"] else 0
        return parse_selectminds(response.text), state["total"]

    return read


_READERS = {
    "oracle": _oracle_reader,
    "jibe": _jibe_reader,
    "ukg": _ukg_reader,
    "hrsmart": _hrsmart_reader,
    "successfactors": _successfactors_reader,
    "erecruit": _erecruit_reader,
    "peoplesoft": _peoplesoft_reader,
    "taleo": _taleo_reader,
    "adp": _adp_reader,
    "tbe": _tbe_reader,
    "cornerstone": _cornerstone_reader,
    "avanti": _avanti_reader,
    "aspnet_jobsearch": _aspnet_jobsearch_reader,
    "selectminds": _selectminds_reader,
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
                    if hint and hint.lower() not in (row.get("location") or "").lower():
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
