"""Oracle Recruiting Cloud (Candidate Experience) — one reader for every tenant.

Every Oracle-hosted career site is a single-page app over the same public REST
search, so one request shape serves them all::

    GET https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList
        &finder=findReqs;siteNumber=<site>,limit=<n>,offset=<n>,sortBy=POSTING_DATES_DESC

    -> {"items": [{"TotalJobsCount": N, "requisitionList": [{Id, Title,
        PrimaryLocation, PostedDate, PostingEndDate, ...}]}]}

Verified 2026-09-27 against three tenants with exactly this request — the
Government of New Brunswick (``emgi.fa.ca3`` / ``CX_1001``, 27 postings),
J.D. Irving (``hcpd.fa.ca2`` / ``CX_5003``, 736) and the Government of Nunavut
(``fa-exux-saasfaprod1.fa.ocs`` / ``CX_5``, 92). The site's own UI adds facet
and flex-field expansions; none of them is needed to list postings. ``limit``
is capped at 200 by the API.

Host and site number are read off the career site's own URL
(``.../hcmUI/CandidateExperience/en/sites/<site>/...``), so a new employer is
added by pasting that URL into portals.yml — the same idiom as Workday.

Two tiers use this: ``gov_boards`` for public-sector tenants (NB, Nunavut) and
``employer_boards`` for private employers (J.D. Irving). Which tier a tenant
belongs to decides whether its titles face the positive title filter.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

_API = "{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
_JOB_URL = "{base}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}"
_SITE_RE = re.compile(
    r"^(?P<base>https://[^/]+\.oraclecloud\.com)/hcmUI/CandidateExperience/"
    r"[a-z]{2}(?:-[A-Za-z]{2})?/sites/(?P<site>[^/?#]+)"
)
_TAG_RE = re.compile(r"<[^>]+>")
MAX_PAGE_SIZE = 200


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", str(value or "")))).strip()


def resolve_oracle_site(url: str) -> tuple[str, str] | None:
    """``(base, site)`` from any Candidate Experience URL, or None."""
    match = _SITE_RE.match((url or "").strip())
    return (match.group("base"), match.group("site")) if match else None


def job_url(base: str, site: str, job_id: str) -> str:
    return _JOB_URL.format(base=base, site=site, job_id=job_id)


def parse_requisitions(
    payload: dict[str, Any] | None, *, base: str, site: str, company: str
) -> list[dict[str, str]]:
    """Extract job rows from a search response.

    The payload nests one search result per ``items`` entry, each carrying the
    postings under ``requisitionList``; everything above that level is facet
    and paging metadata.
    """
    rows: list[dict[str, str]] = []
    for item in (payload or {}).get("items") or []:
        for req in item.get("requisitionList") or []:
            # One null requisition must not cost the page's others; the
            # caller counts it as lost against the raw list length.
            if not isinstance(req, dict):
                continue
            job_id = str(req.get("Id") or "").strip()
            title = _text(req.get("Title"))
            if not job_id or not title:
                continue
            rows.append(
                {
                    "url": job_url(base, site, job_id),
                    "title": title,
                    "company": company,
                    "location": _text(req.get("PrimaryLocation")),
                    "salary": "",
                    # The list endpoint returns a null PostingEndDate for most
                    # tenants; the real closing date is only on the detail
                    # endpoint, so this stays empty rather than pretending to
                    # be "until filled". PostedDate, however, is populated.
                    "closes": _text(req.get("PostingEndDate")),
                    "posted": _text(req.get("PostedDate")),
                }
            )
    return rows


def requisitions_total(payload: dict[str, Any] | None) -> int:
    """Result count the API reports for the query, or 0 when absent."""
    for item in (payload or {}).get("items") or []:
        count = item.get("TotalJobsCount")
        if isinstance(count, int):
            return count
    return 0


def fetch_requisitions(
    client: httpx.Client,
    base: str,
    site: str,
    *,
    offset: int,
    limit: int,
    keyword: str | None = None,
) -> dict[str, Any] | None:
    """One page of search results, or None on any failure.

    Oracle takes paging and the search term inside one ``finder`` string, not
    as separate query parameters.
    """
    finder = [f"siteNumber={site}"]
    if keyword:
        finder.append(f"keyword={keyword}")
    finder += [f"limit={limit}", f"offset={offset}", "sortBy=POSTING_DATES_DESC"]
    try:
        response = client.get(
            _API.format(base=base),
            params={
                "onlyData": "true",
                "expand": "requisitionList",
                "finder": "findReqs;" + ",".join(finder),
            },
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    # Even an empty search answers with one item holding an empty
    # `requisitionList` (checked 2026-09-27). Anything else — no items, or an
    # item that is an error message — is a failed read, not a quiet board.
    items = payload.get("items") if isinstance(payload, dict) else None
    if not items or not isinstance(items, list) or not all(
        isinstance(item, dict) and isinstance(item.get("requisitionList"), list) for item in items
    ):
        return None
    return payload
