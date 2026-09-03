"""Direct adapters for public-sector job boards. No WebSearch, no API quota.

Both boards here are immigration-relevant in a way a generic aggregator is
not: the GNWT is an NTNP-eligible territorial-government employer, and the
Nova Scotia public service sits inside the AIP region. Both render their
result lists server-side, so a plain GET plus a parser is enough.

Structures differ, so each board gets its own parser:

- **GNWT** (``gov.nt.ca/careers``) is Drupal Views markup: one
  ``views-row`` per job wrapping a ``job-search-result-link`` anchor, with
  salary / location / closing-date in sibling ``views-field-*`` divs. Paged
  via ``?page=N`` (0-indexed), total advertised as "Displaying 1 - 25 of 95".

- **Nova Scotia** (``jobs.novascotia.ca``) is an SAP SuccessFactors career
  site: a results table of ``tr.data-row``. Each job is emitted **twice** —
  once in a ``hidden-phone`` span and once in ``visible-phone`` — so rows are
  de-duplicated by URL.

- **New Brunswick** (Oracle Cloud Recruiting) exposes a public REST endpoint,
  so this board returns JSON rather than markup. The old ``ere.gnb.ca`` site
  is now only an ASP.NET postback that redirects into the Oracle portal —
  neither curl nor a plain fetch reaches a posting through it, which is why
  the API is used directly. NB sits in the AIP region.

- **Manitoba** (``jobsearch.gov.mb.ca``) publishes one **RSS feed per job
  category**, so it is selected by category id instead of by keyword: 14 is
  Information Technology, 15 Internships, 19 Policy/Planning/Research, 21
  Science and Engineering. Item bodies are ``<strong>Label</strong>: value``
  pairs. Manitoba is an MPNP province.

Boards that could **not** be added, so nobody re-tries them blindly:
Saskatchewan runs the same Oracle stack but its CDN answers the REST path with
a 404; PEI sits behind Radware bot protection; Yukon returns 403 to any
non-browser agent; and Newfoundland publishes no stable listing URL. All four
need a real browser session, which this tier deliberately does not use.
"""

from __future__ import annotations

import datetime as dt
import html
import functools
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from job_hunt.services.posted_date import relative_age_to_iso

GNWT_BASE = "https://www.gov.nt.ca"
GNWT_SEARCH = f"{GNWT_BASE}/careers/en/search/job"
NS_BASE = "https://jobs.novascotia.ca"
NSHA_BASE = "https://jobs.nshealth.ca"
WRHA_BASE = "https://careers.wrha.mb.ca"
NS_SEARCH = f"{NS_BASE}/search/"
NB_HOST = "https://emgi.fa.ca3.oraclecloud.com"
NB_API = f"{NB_HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
NB_SITE = "CX_1001"
NB_JOB_URL = f"{NB_HOST}/hcmUI/CandidateExperience/en/sites/{NB_SITE}/job/{{job_id}}"
MB_FEED = "https://jobsearch.gov.mb.ca/rss/newJobPostingsFeed.rss"
# Oracle caps a page at 200; 50 keeps each response small enough to be cheap.
NB_PAGE_SIZE = 50

_USER_AGENT = "Mozilla/5.0 (compatible; job-hunt/0.1; personal job search)"
_TAG_RE = re.compile(r"<[^>]+>")

_GNWT_ROW_RE = re.compile(
    r'<a href="(?P<url>[^"]*?/careers/en/job/\d+)"[^>]*class="job-search-result-link"[^>]*>'
    r"(?P<body>.*?)</a>",
    re.S,
)
_GNWT_TOTAL_RE = re.compile(r"Displaying\s+[\d,]+\s*-\s*[\d,]+\s+of\s+([\d,]+)")
# SuccessFactors career sites. Two tenant-specific shapes have to be tolerated:
# the path may carry a site segment before ``/job/`` (Nova Scotia Health serves
# ``/nsha/job/...`` while the provincial board serves ``/job/...``), and the
# anchor emits ``href`` and ``class`` in either order depending on the theme.
_NS_ROW_RE = re.compile(
    r'<a (?:href="(?P<url>/(?:[^"/]+/)?job/[^"]+)"[^>]*class="jobTitle-link"'
    r'|class="jobTitle-link"[^>]*href="(?P<url2>/(?:[^"/]+/)?job/[^"]+)")[^>]*>'
    r"(?P<title>.*?)</a>",
    re.S,
)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


# GNWT prints how long ago a job was posted rather than the date ("17 hours
# ago", "3 days ago"). Freshness is one of the four axes triage ranks on, so
# an absent date costs the whole board its recency signal. Workday needs the
# same "N units ago" arithmetic for its own "Posted N Days Ago" phrasing, so
# it lives in job_hunt.services.posted_date; this stays as a thin wrapper so
# the call site below reads the same as it always has.
def _age_to_iso(fragment: str, today: dt.date | None = None) -> str:
    """ISO date for a "N units ago" interval, or "" when it is not one."""
    return relative_age_to_iso(fragment, today)


def _field(body: str, css_class: str) -> str:
    match = re.search(rf'<div class="{css_class}">(.*?)</div>', body, re.S)
    return _text(match.group(1)) if match else ""


def parse_gnwt(page_html: str) -> list[dict[str, str]]:
    """Extract job rows from a GNWT careers search page."""
    rows: list[dict[str, str]] = []
    for match in _GNWT_ROW_RE.finditer(page_html or ""):
        body = match.group("body")
        title_match = re.search(r"<h3>(.*?)</h3>", body, re.S)
        # Falling back to the anchor text matters more than it looks: a row the
        # parser cannot title is a posting that vanishes with no trace, and the
        # heading tag is the most likely thing to change in a Drupal upgrade.
        title = _text(title_match.group(1)) if title_match else _text(body)
        if not title:
            continue
        closing = _field(body, "views-field-field-job-closing-date")
        rows.append(
            {
                "url": urljoin(GNWT_BASE, match.group("url")),
                "title": title,
                "company": "Government of the Northwest Territories",
                "location": _field(body, "views-field-field-job-location"),
                "salary": _field(body, "views-field-field-job-salary-range"),
                "closes": closing.removeprefix("Closes:").strip(),
                "posted": _age_to_iso(_field(body, "views-field-field-job-import-updated")),
            }
        )
    return rows


def gnwt_total(page_html: str) -> int:
    """Result count advertised by the page, or 0 when absent."""
    match = _GNWT_TOTAL_RE.search(page_html or "")
    return int(match.group(1).replace(",", "")) if match else 0


def parse_successfactors(
    page_html: str,
    *,
    base: str = NS_BASE,
    company: str = "Government of Nova Scotia",
) -> list[dict[str, str]]:
    """Extract job rows from any SuccessFactors career search page.

    ``base`` and ``company`` are per-tenant: the same markup serves the Nova
    Scotia public service, Nova Scotia Health and the Winnipeg Regional Health
    Authority. Resolving links against a hardcoded base would emit URLs pointing
    at the wrong employer's site, which reads as a live row and 404s on click.
    """
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _NS_ROW_RE.finditer(page_html or ""):
        url = urljoin(base, match.group("url") or match.group("url2"))
        if url in seen:
            continue
        title = _text(match.group("title"))
        if not title:
            continue
        seen.add(url)
        rows.append(
            {
                "url": url,
                "title": title,
                "company": company,
                "location": _ns_location(page_html, match.end()),
                "salary": "",
                "closes": "",
            }
        )
    return rows


def parse_ns_gov(page_html: str) -> list[dict[str, str]]:
    """Extract job rows from a Nova Scotia public-service search page."""
    return parse_successfactors(page_html)


def _ns_location(page_html: str, from_index: int) -> str:
    """First ``jobLocation`` span after this row's anchor."""
    match = re.compile(r'<span class="jobLocation">(.*?)</span>', re.S).search(
        page_html, from_index
    )
    if not match or match.start() - from_index > 2000:
        return ""
    return _text(match.group(1))


def parse_nb_gov(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    """Extract job rows from an Oracle Cloud Recruiting search response.

    The payload nests one search result per ``items`` entry, each carrying the
    postings under ``requisitionList``; everything above that level is facet
    and paging metadata.
    """
    rows: list[dict[str, str]] = []
    for item in (payload or {}).get("items") or []:
        for req in item.get("requisitionList") or []:
            job_id = str(req.get("Id") or "").strip()
            title = _text(str(req.get("Title") or ""))
            if not job_id or not title:
                continue
            rows.append(
                {
                    "url": NB_JOB_URL.format(job_id=job_id),
                    "title": title,
                    "company": "Government of New Brunswick",
                    "location": _text(str(req.get("PrimaryLocation") or "")),
                    "salary": "",
                    # The list endpoint always returns a null PostingEndDate;
                    # the real closing date is only on the detail endpoint, so
                    # this stays empty rather than pretending to be "until
                    # filled". PostedDate, however, is populated here.
                    "closes": _text(str(req.get("PostingEndDate") or "")),
                    "posted": _text(str(req.get("PostedDate") or "")),
                }
            )
    return rows


def nb_total(payload: dict[str, Any] | None) -> int:
    """Result count the API reports for the query, or 0 when absent."""
    for item in (payload or {}).get("items") or []:
        count = item.get("TotalJobsCount")
        if isinstance(count, int):
            return count
    return 0


_MB_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
_MB_FIELD_RE = re.compile(r"<strong>(?P<label>[^<]+)</strong>\s*:\s*(?P<value>.*?)(?:<br\s*/?>|$)", re.S)
# The feed is bilingual: measured 2026-08-12, 59 English items and 5 French ones
# in the same category-less feed. Keying on the English label alone returned an
# empty location and closing date for every French posting, which is
# indistinguishable downstream from a posting that genuinely has neither.
_MB_LABELS = {
    "location": ("location(s)", "lieu(s)", "location", "work location"),
    "salary": ("salary(s)", "salaire(s)", "salary"),
    "closes": ("closing date", "date de clôture", "date de cloture", "closing"),
    "posted": ("posted date", "date d'affichage", "date de publication", "posted"),
}


def _mb_field(fields: dict[str, str], key: str) -> str:
    for label in _MB_LABELS[key]:
        value = fields.get(label)
        if value:
            return value
    return ""


def parse_mb_gov(feed_xml: str) -> list[dict[str, str]]:
    """Extract job rows from a Manitoba government RSS category feed."""
    rows: list[dict[str, str]] = []
    for body in _MB_ITEM_RE.findall(feed_xml or ""):
        title_match = re.search(r"<title>(.*?)</title>", body, re.S)
        link_match = re.search(r"<link>(.*?)</link>", body, re.S)
        if not title_match or not link_match:
            continue
        title = _text(title_match.group(1))
        if not title:
            continue
        description = html.unescape(
            (re.search(r"<description>(.*?)</description>", body, re.S) or _EMPTY).group(1)
        )
        fields = {
            _text(m.group("label")).lower(): _text(m.group("value"))
            for m in _MB_FIELD_RE.finditer(description)
        }
        rows.append(
            {
                "url": html.unescape(_text(link_match.group(1))),
                "title": title,
                "company": "Government of Manitoba",
                "location": _mb_field(fields, "location"),
                "salary": _mb_field(fields, "salary"),
                "closes": _mb_field(fields, "closes"),
                "posted": _mb_field(fields, "posted"),
            }
        )
    return rows


class _EmptyMatch:
    @staticmethod
    def group(_index: int) -> str:
        return ""


_EMPTY = _EmptyMatch()


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> str:
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError:
        return ""
    return response.text if response.status_code == 200 else ""


def scan_gov_boards(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep=time.sleep,
    stats: dict[str, dict[str, Any]] | None = None,
    title_screener=None,
) -> list[dict[str, str]]:
    """Fetch enabled public-sector boards. Returns de-duplicated rows.

    Pass ``stats`` to find out whether the sweep was actually complete. Each
    board reports ``collected``, the ``advertised`` total where the board
    publishes one, and ``truncated`` — True when the page budget ran out with
    rows still coming, which is the failure mode that silently loses postings.
    """
    if not config or not config.get("enabled", False):
        return []
    boards = config.get("boards") or {}
    delay = float(config.get("delay_s", 1.5))
    screen_boards: dict[str, list[dict[str, str]]] = {}
    timeout = float(config.get("timeout_s", 30.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
    try:
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        first = True

        def pause() -> None:
            nonlocal first
            if not first and delay > 0:
                sleep(delay)
            first = False

        def collect(board_id: str, cfg: dict[str, Any], spec: dict[str, Any]) -> None:
            """Run one board, either keyword-scoped or whole-board."""
            keywords = [str(k).strip() for k in (cfg.get("keywords") or []) if str(k).strip()]
            max_pages = int(cfg.get("max_pages", 3))
            # `title_include` is the precision gate for whole-organisation
            # employers. The source's own search matches the posting BODY, so on
            # a health authority `q=data` returns Radiation Therapist and
            # `q=engineer` returns 5th Class Power Engineer — measured, not
            # assumed. Tiers 4-7 deliberately skip the positive title filter
            # downstream, so a board this noisy has to gate its own titles or it
            # floods the pipeline with clinical postings.
            title_include = [
                str(t).strip().lower() for t in (cfg.get("title_include") or []) if str(t).strip()
            ]
            # `title_screen` hands the board's titles to a model instead of a
            # substring list. Measured 2026-08-15 on the real corpus: the
            # substring gate kept 15 titles of which 9 were noise (seven boiler
            # operators among them); the model kept 7, all of them relevant, and
            # additionally recovered one the substring list had dropped.
            wants_screen = bool(cfg.get("title_screen")) and title_screener is not None
            dropped = 0
            # Manitoba selects by RSS category id rather than by keyword; the
            # ids stand in for query terms so the same loop drives every board.
            if spec.get("selector_key"):
                keywords = [str(k).strip() for k in (cfg.get(spec["selector_key"]) or []) if str(k).strip()]
            # Keyword mode issues one query per term and unions the results.
            # Whole-board mode walks the pages with no query at all.
            queries: list[str | None] = list(keywords) if keywords else [None]
            board_urls: set[str] = set()
            truncated = False
            for keyword in queries:
                for page in range(max_pages):
                    pause()
                    rows = spec["fetch"](client, keyword, page)
                    if not rows:
                        break
                    # Rows on the final allowed page means the budget, not the
                    # board, ended the walk.
                    if page == max_pages - 1:
                        truncated = True
                    for row in rows:
                        board_urls.add(row["url"])
                        if row["url"] in seen:
                            continue
                        seen.add(row["url"])
                        row["board"] = board_id
                        row["matched_keyword"] = keyword or ""
                        if wants_screen:
                            screen_boards.setdefault(board_id, []).append(row)
                            continue
                        if title_include and not any(
                            term in row["title"].lower() for term in title_include
                        ):
                            dropped += 1
                            continue
                        out.append(row)
                    # A keyword query rarely fills more than one page.
                    if keyword and len(rows) < 25:
                        truncated = False
                        break
            if stats is not None:
                total = advertised.get(board_id)
                # A board that publishes its own result count settles the
                # question; the page heuristic is only a fallback for boards
                # that do not (Nova Scotia and Manitoba publish no total).
                if total is not None and not keywords:
                    truncated = len(board_urls) < total
                stats[board_id] = {
                    "collected": len(board_urls),
                    "advertised": total,
                    "truncated": truncated,
                    "mode": "keyword" if keywords else "whole-board",
                    "errors": errors.get(board_id, 0),
                    # Rows the title gate removed. Reported so a mis-tuned
                    # `title_include` shows up as a number rather than as a
                    # board that quietly looks empty.
                    "title_filtered": dropped,
                }

        # `keyword_param` searches the posting *body*, not just the title —
        # verified 2026-08-06: GNWT `keywords=data` returns "Territorial
        # Statistician" (no "data" in the title) and NS `q=data` returns
        # "Product Director - Early Years". That is why these boards are
        # scoped at the source instead of by post-hoc title matching: they are
        # whole-organisation boards where a title allowlist misses public
        # sector naming and a title denylist never ends.
        # NS ignores `searchByKeyword`; the working parameter is `q`.
        # Boards that publish a result count record it here as they fetch, so
        # coverage can be checked against the board's own number rather than
        # against an assumption about page size.
        advertised: dict[str, int] = {}
        # A 503 and an empty board both yield zero rows. Counting failed
        # requests is what keeps "the sweep found nothing" from being reported
        # as "there is nothing to find".
        errors: dict[str, int] = {}

        def html_board(url: str, keyword_param: str, page_params, parse, total_fn=None, key=""):
            def fetch(http: httpx.Client, keyword: str | None, page: int):
                params: dict[str, Any] = {}
                if keyword:
                    params[keyword_param] = keyword
                if page:
                    params.update(page_params(page))
                body = _get(http, url, params)
                if body == "":
                    errors[key] = errors.get(key, 0) + 1
                if total_fn and not keyword:
                    total = total_fn(body)
                    if total:
                        advertised[key] = total
                return parse(body)

            return fetch

        def nb_fetch(http: httpx.Client, keyword: str | None, page: int):
            # Oracle takes paging and the search term inside one `finder` string,
            # not as separate query parameters.
            finder = [f"siteNumber={NB_SITE}"]
            if keyword:
                finder.append(f"keyword={keyword}")
            finder += [f"limit={NB_PAGE_SIZE}", f"offset={page * NB_PAGE_SIZE}",
                       "sortBy=POSTING_DATES_DESC"]
            try:
                response = http.get(
                    NB_API,
                    params={
                        "onlyData": "true",
                        "expand": "requisitionList",
                        "finder": "findReqs;" + ",".join(finder),
                    },
                )
                if response.status_code != 200:
                    errors["nb_gov"] = errors.get("nb_gov", 0) + 1
                    return []
                payload = response.json()
                if not keyword:
                    advertised["nb_gov"] = nb_total(payload)
                return parse_nb_gov(payload)
            except (httpx.HTTPError, ValueError):
                errors["nb_gov"] = errors.get("nb_gov", 0) + 1
                return []

        def mb_fetch(http: httpx.Client, category: str | None, page: int):
            # One feed per category, no paging — a second page is always empty.
            if page:
                return []
            params = {"categoryID": category} if category else {}
            body = _get(http, MB_FEED, params)
            if body == "":
                errors["mb_gov"] = errors.get("mb_gov", 0) + 1
            return parse_mb_gov(body)

        specs = {
            "gnwt": {
                "fetch": html_board(
                    GNWT_SEARCH, "keywords", lambda page: {"page": page}, parse_gnwt,
                    total_fn=gnwt_total, key="gnwt",
                ),
            },
            "ns_gov": {
                # SuccessFactors pages by result offset, 25 per page.
                "fetch": html_board(
                    NS_SEARCH, "q", lambda page: {"startrow": page * 25}, parse_ns_gov,
                    key="ns_gov",
                ),
            },
            "nb_gov": {"fetch": nb_fetch},
            "mb_gov": {"fetch": mb_fetch, "selector_key": "categories"},
            # Health authorities on the same SuccessFactors platform as ns_gov.
            # These boards are dominated by clinical postings — WRHA advertises
            # roughly 800 clinical against 100 non-clinical — so they are only
            # worth scanning with keywords set, never as a whole-board sweep.
            "nsha": {
                "fetch": html_board(
                    f"{NSHA_BASE}/search/", "q", lambda page: {"startrow": page * 25},
                    functools.partial(
                        parse_successfactors, base=NSHA_BASE, company="Nova Scotia Health"
                    ),
                    key="nsha",
                ),
            },
            "wrha": {
                "fetch": html_board(
                    f"{WRHA_BASE}/search/", "q", lambda page: {"startrow": page * 25},
                    functools.partial(
                        parse_successfactors,
                        base=WRHA_BASE,
                        company="Winnipeg Regional Health Authority",
                    ),
                    key="wrha",
                ),
            },
        }
        for board_id, spec in specs.items():
            cfg = boards.get(board_id) or {}
            if cfg.get("enabled", False):
                collect(board_id, cfg, spec)

        # One screening call per board, after collection, so the model sees the
        # whole title list at once and the run costs one request per board.
        for board_id, rows in screen_boards.items():
            titles = [row["title"] for row in rows]
            kept = title_screener(titles)
            if kept is None:
                # No answer: keep everything. A board that silently returns
                # nothing is indistinguishable from a board with no openings.
                out.extend(rows)
                if stats is not None and board_id in stats:
                    stats[board_id]["title_screen"] = "unavailable — kept all"
                continue
            survivors = [row for row in rows if row["title"] in kept]
            out.extend(survivors)
            if stats is not None and board_id in stats:
                stats[board_id]["title_filtered"] = len(rows) - len(survivors)
                stats[board_id]["title_screen"] = "model"

        return out
    finally:
        if owns_client:
            client.close()
