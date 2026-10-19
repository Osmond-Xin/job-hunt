"""Tier 9: Getro-powered accelerator and VC portfolio job boards.

Why this exists: the operator's own read of his record, 2026-09-17 — "我很适合
初创企业。我的工作简历和背景也都是初创企业的" — and the boards where early-stage
Canadian companies actually post are not the national aggregators. They are the
portfolio boards of the funds and hubs that back them: Communitech, MaRS,
Inovia, Real Ventures, Antler. Those five (and most of the others found that
day) run on Getro, which means one reader covers all of them.

The API behind every Getro board::

    POST https://api.getro.com/api/v2/collections/<network_id>/search/jobs
    Accept: application/json
    {"hitsPerPage": 20, "page": 0, "query": "ai engineer",
     "filters": {"searchable_locations": ["Canada"]}}

    -> {"results": {"count": 1183, "jobs": [{title, url, created_at,
        searchable_locations, organization: {name}, work_mode, seniority, ...}]}}

Two measured facts shape the design (2026-09-17, against Communitech):

- **`hitsPerPage` is ignored**: 20 rows a page whatever you ask for. Communitech
  alone advertises 1,183 jobs, so paging a whole board is 60 requests for a list
  that is mostly not his work — the board carries EY tax lawyers next to ML
  engineers. The tier therefore searches with a list of role queries instead of
  walking the board, and caps pages per query.
- **`filters.job_functions` matches nothing** (the facet wants values this
  module has no way to enumerate), while `filters.searchable_locations` works:
  "Canada" narrowed Communitech from 1,183 to 1,007. Location is filtered
  server-side; the occupation is left to the query and to the caller's title
  filter.

Unlike the Chinese boards (tier 8), these titles are in English and the boards
are general — so scan.py runs these rows through the **positive** title filter
rather than waving them past it.

Two more facts, measured 2026-10-19 on ~1,000 rows across all configured boards:

- **A portfolio board is not a start-up board.** A third to a half of its rows
  are from companies past that stage — ``organization.stage`` "ipo" (Google, Okta,
  GitLab), "acquisition", "private_equity" — or with a ``head_count`` bucket of
  5+ (1,001+ staff: KPMG, EY, Scotiabank, Stripe). Those rows are kept but
  labelled ``getro_mature``, so triage's start-up lane does not count them.
- **``organization.name`` can name the wrong employer.** Getro files an acquired
  company's postings under the acquiree: Coveo's roles appear as "Qubit.", Helm.ai's
  as "One Way Ventures". The posting URL usually names the real employer (its ATS
  slug, or LinkedIn's "...-at-<company>-<id>"), so when that slug disagrees with
  the organization, the slug wins — which also lets one-role-per-employer see it.

The network id is not the board's slug: it is the integer in
``props.pageProps.network.id`` of the board's own HTML. Recorded per board in
``portals.yml`` so this module never has to scrape it.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row

GETRO_SEARCH = "https://api.getro.com/api/v2/collections/{network_id}/search/jobs"
_PAGE_SIZE = 20
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def _posted(created_at: Any) -> str:
    """``created_at`` is a unix timestamp; the pipeline stores ISO dates."""
    try:
        return datetime.fromtimestamp(float(created_at), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


# Stages and head-count buckets that mean the employer is no longer a start-up.
_MATURE_STAGES = frozenset({"ipo", "acquisition", "private_equity"})
_MATURE_HEAD_COUNT = 5  # Getro's bucket for 1,001-5,000 staff; 6 is 5,001+


def _mature(organization: dict[str, Any]) -> bool:
    if str(organization.get("stage") or "").lower() in _MATURE_STAGES:
        return True
    try:
        return int(organization.get("head_count") or 0) >= _MATURE_HEAD_COUNT
    except (TypeError, ValueError):
        return False


# The employer as the posting URL names it.
_URL_EMPLOYER_RES = (
    re.compile(r"linkedin\.com/jobs/view/[^/?#]*-at-([a-z0-9-]+?)-\d+(?:[/?#]|$)", re.I),
    re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.I),
    # An embed URL names the board in its ``for=`` parameter, wherever that sits
    # in the query; any other embed URL names no employer and is skipped.
    re.compile(r"greenhouse\.io/embed/[^?#]*\?(?:[^#]*&)?for=([^&#]+)", re.I),
    re.compile(r"greenhouse\.io/(?!embed/)([^/?#&]+)", re.I),
    re.compile(r"jobs\.lever\.co/([^/?#]+)", re.I),
    re.compile(r"apply\.workable\.com/(?!api/)([^/?#]+)", re.I),
)


def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _shared_run(a: str, b: str) -> int:
    """Length of the longest run of characters the two strings share."""
    if not a or not b:
        return 0
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return match.size


def _initials(name: str) -> set[str]:
    """"Royal Bank of Canada" -> {"rboc", "rbc"}: a slug that abbreviates the name
    is the same employer, not another one (spec review 2026-10-19: RBC, MLSE)."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    significant = [word for word in words if word not in {"of", "and", "the", "for"}]
    return {"".join(word[0] for word in words), "".join(word[0] for word in significant)} - {""}


def _employer(job: dict[str, Any]) -> str:
    """The hiring company: Getro's organization, unless the URL names another."""
    name = str((job.get("organization") or {}).get("name") or "").strip()
    url = str(job.get("url") or "")
    for pattern in _URL_EMPLOYER_RES:
        match = pattern.search(url)
        if not match:
            continue
        slug = match.group(1)
        squashed_slug, squashed_name = _squash(slug), _squash(name)
        derived = " ".join(part.capitalize() for part in re.split(r"[-_]+", slug) if part)
        if not squashed_name:
            return derived if len(squashed_slug) >= 3 else name
        if squashed_slug in _initials(name):
            return name
        # A rebrand keeps a stem ("Fellow.app" -> fellowai, "GlossGenius" ->
        # geniusai); an acquirer shares nothing with the acquiree.
        if len(squashed_slug) < 3 or _shared_run(squashed_slug, squashed_name) >= min(
            5, len(squashed_slug), len(squashed_name)
        ):
            return name
        return derived
    return name


def _location(job: dict[str, Any]) -> str:
    """The most specific place the board names.

    Getro returns a widening list — "Toronto, ON, Canada", "Ontario, Canada",
    "Canada" — and the last of those is the one `triage.region` cannot place.
    """
    places = [str(p).strip() for p in (job.get("searchable_locations") or job.get("locations") or []) if str(p).strip()]
    if not places:
        return ""
    return max(places, key=lambda place: place.count(","))


def _search(
    client: httpx.Client,
    network_id: int,
    query: str,
    page: int,
    locations: list[str],
    timeout: float,
) -> dict[str, Any]:
    body: dict[str, Any] = {"hitsPerPage": _PAGE_SIZE, "page": page, "query": query, "filters": {}}
    if locations:
        body["filters"] = {"searchable_locations": locations}
    response = client.post(
        GETRO_SEARCH.format(network_id=network_id),
        json=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("results") or {}


def scan_getro_boards_source(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SourceResult:
    """Early-stage postings from every configured Getro board, as one source."""
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="getro_boards", ok=True))

    boards = [board for board in (config.get("boards") or []) if board.get("enabled", True)]
    queries = [str(q).strip() for q in (config.get("queries") or []) if str(q).strip()]
    locations = [str(place) for place in (config.get("locations") or [])]
    max_pages = max(1, int(config.get("max_pages", 3)))
    delay = float(config.get("delay_s", 0.5))
    timeout = float(config.get("timeout_s", 30.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=timeout, follow_redirects=True)

    postings: list[JobPosting] = []
    seen_urls: set[str] = set()
    errors = 0
    truncated = False
    advertised = 0
    try:
        for board in boards:
            network_id = board.get("network_id")
            if not network_id:
                errors += 1
                continue
            board_id = str(board.get("id") or network_id)
            for query in queries:
                for page in range(max_pages):
                    try:
                        results = _search(client, int(network_id), query, page, locations, timeout)
                    except Exception:  # noqa: BLE001 - one failed page is not a quiet board
                        errors += 1
                        break
                    jobs = results.get("jobs") or []
                    count = int(results.get("count") or 0)
                    if page == 0:
                        advertised += count
                    for job in jobs:
                        url = str(job.get("url") or "").strip()
                        if not url or url in seen_urls:
                            continue
                        posting = from_row(
                            {
                                "url": url,
                                "title": str(job.get("title") or "").strip(),
                                "company": _employer(job),
                                "location": _location(job),
                                "posted": _posted(job.get("created_at")),
                                "source": f"getro:{board_id}",
                            },
                            source_id=f"getro:{board_id}",
                            portal="getro_mature" if _mature(job.get("organization") or {}) else "getro",
                        )
                        if posting is None:
                            continue
                        seen_urls.add(url)
                        postings.append(posting)
                    if len(jobs) < _PAGE_SIZE or (page + 1) * _PAGE_SIZE >= count:
                        break
                    if page + 1 == max_pages:
                        truncated = True
                    if delay:
                        sleep(delay)
    finally:
        if owns_client:
            client.close()

    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="getro_boards",
            ok=errors == 0,
            collected=len(postings),
            advertised=advertised or None,
            truncated=truncated,
            errors=errors,
        ),
    )
