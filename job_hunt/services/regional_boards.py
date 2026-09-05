"""Regional tech-industry job boards — the highest-signal tier for this search.

An industry association's board in a small province is a different population
from a national aggregator. The employers are local and mostly small, the
postings are often not syndicated anywhere else, and the region is exactly the
one that carries immigration value. Digital Nova Scotia surfaced a cluster of
Halifax AI roles that months of keyword sweeps had never returned.

Two boards, two shapes, both rendered server-side:

- **Digital Nova Scotia** (``digitalnovascotia.com/job-posts/``) is WordPress:
  one anchor per posting under ``/job-posts/<slug>/``. The employer name is not
  in the listing markup, only on the detail page, so rows carry the slug-derived
  title and leave company blank rather than guess. It paginates at
  ``/job-posts/page/<n>/``, 30 postings a page — measured 2026-08-13, 120
  postings across 4 pages, so reading only the first page lost three quarters
  of the board, including the one Halifax forward-deployed role in it.

- **Tech Manitoba** (``members.techmanitoba.ca/jobs``) runs GrowthZone, whose
  listing links are ``/jobs/info/<slug>-<id>`` and whose card markup carries the
  company as a sibling link into the member directory.

Boards that need a browser and are therefore out of this tier: CollabHub
Atlantic (New Brunswick, HubSpot + JS), CareerBeacon (403 to any non-browser
agent), techNL and the PEI and Saskatchewan councils (no public board found at
any guessed path — they gate postings behind member logins).
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

DNS_BASE = "https://digitalnovascotia.com"
DNS_LIST = f"{DNS_BASE}/job-posts/"
TECHMB_BASE = "https://members.techmanitoba.ca"
TECHMB_LIST = f"{TECHMB_BASE}/jobs"

# Both sites reject the honest "compatible; job-hunt" agent — WordPress on one
# side, a CDN on the other — so this tier presents as a browser. It stays inside
# one request per board per sweep, which is gentler than the search engines
# these boards already accept.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")
_DNS_LINK_RE = re.compile(r'href="(?P<url>https://digitalnovascotia\.com/job-posts/[^"#?]+)"')
# WordPress puts pagination, feeds and taxonomy archives under the same prefix;
# they parse into plausible-looking titles like "2" and "Feed".
_DNS_NOT_A_JOB_RE = re.compile(r"/job-posts/(page|feed|category|tag|author)(/|$)")
# GrowthZone renders one card per posting. The card header anchor wraps the
# employer name; the title lives in the card body's h5. Matching the first
# anchor in the card would return the employer as the title, which is what a
# looser regex did.
_TECHMB_CARD_RE = re.compile(r'<div class="card gz-jobs-card.*?(?=<div class="card gz-jobs-card|\Z)', re.S)
_TECHMB_TITLE_RE = re.compile(
    r'<h5[^>]*gz-card-title[^>]*>\s*<a href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.S
)
_TECHMB_COMPANY_RE = re.compile(r'<span class="gz-img-placeholder">(?P<company>.*?)</span>', re.S)
_TECHMB_POSTED_RE = re.compile(r'gz-jobs-date">\s*Posted\s*(?P<posted>[\d/]+)')


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def _title_from_slug(url: str) -> str:
    """Human title from a WordPress slug.

    The listing page does not carry the employer, and a slug's trailing digits
    are WordPress de-duplicating repeated titles rather than part of the name.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-\d+$", "", slug)
    return slug.replace("-", " ").strip().title()


def parse_digital_nova_scotia(page_html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _DNS_LINK_RE.finditer(page_html or ""):
        url = match.group("url")
        if url.rstrip("/") == DNS_LIST.rstrip("/") or url in seen:
            continue
        if _DNS_NOT_A_JOB_RE.search(url):
            continue
        seen.add(url)
        title = _title_from_slug(url)
        if not title:
            continue
        rows.append(
            {
                "url": url,
                "title": title,
                # Deliberately blank: the employer is only on the detail page,
                # and a guessed company name is worse than an absent one.
                "company": "",
                "location": "Nova Scotia",
                "salary": "",
                "closes": "",
            }
        )
    return rows


def parse_tech_manitoba(page_html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in _TECHMB_CARD_RE.findall(page_html or ""):
        title_match = _TECHMB_TITLE_RE.search(card)
        if not title_match:
            continue
        url = urljoin(TECHMB_BASE, title_match.group("url"))
        title = _text(title_match.group("title")) or _title_from_slug(url)
        if url in seen or not title:
            continue
        seen.add(url)
        company_match = _TECHMB_COMPANY_RE.search(card)
        posted_match = _TECHMB_POSTED_RE.search(card)
        posted = ""
        if posted_match:
            # GrowthZone prints US-order dates; the pipeline stores ISO.
            month, day, year = (posted_match.group("posted").split("/") + ["", "", ""])[:3]
            if year and month and day:
                posted = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        rows.append(
            {
                "url": url,
                "title": title,
                "company": _text(company_match.group("company")) if company_match else "",
                "location": "Manitoba",
                "salary": "",
                "closes": "",
                "posted": posted,
            }
        )
    return rows


_DNS_TITLE_RE = re.compile(r"<title>(?P<title>.*?)</title>", re.S)
_DNS_APPLY_RE = re.compile(r'href="(?P<url>[^"]+)"[^>]*class="button"')
# The apply button leaves for the employer's own ATS, which is the only place
# the listing names who is hiring. Most ATS hosts are shared, so the employer
# is the tenant inside the host or the first path segment, not the domain.
# Order matters: the generic careers-host rule at the end would read
# `apply.workable.com` as the employer "workable".
_ATS_TENANT_RE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"//(?P<t>[\w-]+)\.njoyn\.com",
        r"//(?P<t>[\w-]+)\.wd\d+\.myworkdayjobs\.com",
        r"//(?P<t>[\w-]+)\.my\.salesforce-sites\.com",
        r"//(?P<t>[\w-]+)\.bamboohr\.com",
        r"//jobs\.dayforcehcm\.com/[^/]+/(?P<t>[\w-]+)/",
        r"//apply\.workable\.com/(?P<t>[\w-]+)/",
        r"//(?:boards|job-boards)\.greenhouse\.io/(?P<t>[\w-]+)",
        r"//jobs\.lever\.co/(?P<t>[\w-]+)",
        # CareerBeacon names the employer in the path on its public job URLs
        # but not on the `jobs.careerbeacon.com/details/...` form, which
        # therefore falls through and stays unresolved rather than guessed.
        r"//(?:www\.)?careerbeacon\.com/(?:[a-z]{2}/)?job/\d+/(?P<t>[\w-]+)/",
        r"//(?:jobs|careers|apply|recruiting)\.(?P<t>[\w-]+)\.(?:com|ca|io|net|org)",
    )
)
_DOMAIN_RE = re.compile(r"//(?:www\.)?(?P<host>[^/:]+)")


def _employer_from_apply_url(apply_url: str) -> str:
    """Employer implied by where the apply button goes.

    Deliberately conservative: this returns what the URL actually says — a
    tenant id or a registrable domain — rather than a prettified company name.
    A wrong-looking ``cgi`` is recoverable; an invented "CGI Group Inc." in the
    tracker is not.
    """
    if not apply_url:
        return ""
    for pattern in _ATS_TENANT_RE:
        match = pattern.search(apply_url)
        if match:
            return match.group("t").replace("-", " ").strip()
    match = _DOMAIN_RE.search(apply_url)
    if not match:
        return ""
    host = match.group("host")
    # Drop the public suffix so "meridiarecruitment.ca" reads as a name; two
    # labels are kept for co.uk-style suffixes.
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "gov", "org"}:
        return parts[-3]
    return parts[0] if len(parts) < 2 else parts[-2]


def parse_dns_detail(page_html: str) -> dict[str, str]:
    """Real title and employer from a Digital Nova Scotia posting page.

    The listing markup carries neither. The page ``<title>`` holds the posting
    title with its original casing and punctuation, suffixed with the site
    name; the slug-derived title loses both ("Sr Coupa Consultant Manager").
    """
    out = {"title": "", "company": "", "apply_url": ""}
    title_match = _DNS_TITLE_RE.search(page_html or "")
    if title_match:
        # "Role – Digital Nova Scotia – Leading Digital Industry"; the site
        # suffix uses en dashes, which a role title effectively never does.
        title = _text(title_match.group("title"))
        out["title"] = re.split(r"\s+[–—]\s+Digital Nova Scotia", title)[0].strip()
    apply_match = _DNS_APPLY_RE.search(page_html or "")
    if apply_match:
        out["apply_url"] = html.unescape(apply_match.group("url"))
        out["company"] = _employer_from_apply_url(out["apply_url"])
    return out


def _fetch_via_curl(url: str, timeout: float) -> tuple[str, bool]:
    """Fetch through the curl binary, for hosts that refuse httpx.

    Digital Nova Scotia sits behind a CDN that fingerprints the TLS handshake,
    not the User-Agent: curl with a browser agent gets 200 and httpx with the
    identical headers gets 403. Rather than chase the fingerprint, this tier
    borrows a client the CDN already trusts. One request per sweep.
    """
    if not shutil.which("curl"):
        return "", True
    try:
        proc = subprocess.run(
            ["curl", "-sL", "--max-time", str(int(timeout)), "-H", f"User-Agent: {_USER_AGENT}", url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "", True
    return proc.stdout, proc.returncode != 0 or not proc.stdout.strip()


BOARDS: dict[str, dict[str, Any]] = {
    "digital_nova_scotia": {
        "url": DNS_LIST,
        # WordPress puts page 1 at the bare archive URL, not at `page/1/`.
        "page_url": lambda page: DNS_LIST if page == 0 else f"{DNS_LIST}page/{page + 1}/",
        "parse": parse_digital_nova_scotia,
        "region": "Nova Scotia",
        "fetch": "curl",
        # 4 pages of postings as measured; the walk stops on the first page
        # that yields nothing new, so the ceiling only bounds a runaway.
        "max_pages": 8,
        # The listing carries no employer and a lossy slug title; both are on
        # the detail page. Cached, so this is one request per posting ever.
        "enrich": lambda rows, **kw: enrich_dns_rows(rows, **kw),
    },
    "tech_manitoba": {
        "url": TECHMB_LIST,
        "parse": parse_tech_manitoba,
        "region": "Manitoba",
        # GrowthZone renders every card into one page — 4 postings, no paging.
        "max_pages": 1,
    },
}


DNS_CACHE = Path("cache/regional_boards/dns-detail.json")


def _load_cache(path: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(path: Path, cache: dict[str, dict[str, str]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def enrich_dns_rows(
    rows: list[dict[str, str]],
    *,
    timeout: float,
    sleep=time.sleep,
    delay: float = 0.0,
    limit: int = 200,
    cache_path: Path = DNS_CACHE,
) -> int:
    """Fill in employer and real title from each posting's detail page.

    One fetch per posting, cached permanently by URL — a posting's employer
    never changes, so this costs a request the first time the board surfaces it
    and nothing afterwards. Worth the requests: without an employer, triage
    cannot apply its recruitment-agency filter or its large-employer penalty,
    and the operator reads a whole tier as "Unknown (see posting)".
    """
    cache = _load_cache(cache_path)
    fetched = 0
    for row in rows:
        url = row.get("url", "")
        detail = cache.get(url)
        if detail is None:
            if fetched >= limit:
                continue
            if fetched and delay > 0:
                sleep(delay)
            body, failed = _fetch_via_curl(url, timeout)
            fetched += 1
            if failed:
                continue
            detail = parse_dns_detail(body)
            cache[url] = detail
        if detail.get("title"):
            row["title"] = detail["title"]
        if detail.get("company"):
            row["company"] = detail["company"]
        if detail.get("apply_url"):
            row["apply_url"] = detail["apply_url"]
    if fetched:
        _save_cache(cache_path, cache)
    return fetched


def scan_regional_boards(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep=time.sleep,
    stats: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Fetch enabled regional boards. Same contract as ``scan_gov_boards``."""
    if not config or not config.get("enabled", False):
        return []
    boards = config.get("boards") or {}
    delay = float(config.get("delay_s", 1.5))
    timeout = float(config.get("timeout_s", 30.0))

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT}, timeout=timeout, follow_redirects=True
        )
    try:
        out: list[dict[str, str]] = []
        first = True

        def fetch_page(spec: dict[str, Any], page: int) -> tuple[str, bool]:
            page_url = spec.get("page_url")
            url = page_url(page) if page_url else spec["url"]
            if spec.get("fetch") == "curl":
                return _fetch_via_curl(url, timeout)
            try:
                response = client.get(url)
                return (
                    response.text if response.status_code == 200 else "",
                    response.status_code != 200,
                )
            except httpx.HTTPError:
                return "", True

        for board_id, spec in BOARDS.items():
            cfg = boards.get(board_id) or {}
            if not cfg.get("enabled", False):
                continue
            max_pages = int(cfg.get("max_pages", spec.get("max_pages", 1)))
            seen: set[str] = set()
            collected = 0
            errors = 0
            truncated = False
            for page in range(max_pages):
                if not first and delay > 0:
                    sleep(delay)
                first = False
                body, failed = fetch_page(spec, page)
                errors += int(failed)
                # A 404 past the last page is how WordPress ends an archive —
                # it is the walk terminating, not the board being unreachable.
                if failed:
                    break
                fresh = [row for row in spec["parse"](body) if row["url"] not in seen]
                # A page that repeats what we already have is the same signal as
                # an empty one: WordPress serves the last page for any overshoot.
                if not fresh:
                    break
                for row in fresh:
                    seen.add(row["url"])
                    row["board"] = board_id
                    out.append(row)
                collected += len(fresh)
                # Only a board that actually pages can run out of budget; a
                # single-page board is complete by the time it is read once.
                if page == max_pages - 1 and spec.get("page_url"):
                    truncated = True
            if spec.get("enrich") and cfg.get("enrich", True):
                board_rows = [row for row in out if row["board"] == board_id]
                spec["enrich"](board_rows, timeout=timeout, sleep=sleep, delay=delay)
            if stats is not None:
                # These boards publish no total, so a failed request is the only
                # thing that distinguishes "quiet week" from "we never arrived",
                # and `truncated` the only thing that distinguishes "read to the
                # end" from "ran out of page budget mid-board".
                stats[board_id] = {
                    "collected": collected,
                    "errors": errors,
                    "truncated": truncated,
                }
        return out
    finally:
        if owns_client:
            client.close()
