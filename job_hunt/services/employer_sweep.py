"""Sweep the careers pages of employers that have no board we can read.

Remote-region employers mostly post on their own small sites — a municipality's
"Careers" page, a co-op's WordPress, a tribal council's PDF list — which no
aggregator carries and no platform adapter covers. On 2026-10-24 the remote
lane held 82 technical rows across the whole pipeline, and the one posting that
fitted (Near North DSB) had arrived through Adzuna by chance. The employers
worth sweeping are the ones officially willing to hire immigrants: the RCIP and
AIP designated-employer lists (``data/designated-employers.tsv``).

Each page is read the cheap way, on the operator's ruling the same day ("解析的时
候可以使用minimax"): the page's links are numbered and MiniMax returns only the
numbers of the links that are technical job postings, plus the numbers worth
following one hop (a link out to the employer's ATS, a "current openings"
page). The model never writes a title or a URL, so it cannot invent one; every
row it keeps is an anchor that was on the page.

The interface is ``collect``. Fetching, rendering and the model are injected,
so the whole path — including what is kept and what is reported unreadable —
is tested without a network or mmx. Defects the 2026-10-24 Codex and OpenCode
red teams found, each fixed here: non-posting pages admitted as jobs; a 403
with no curl retry; LinkedIn reachable through redirects and page subresources;
``#/jobs/1`` routes dropped; a JavaScript page with a nav bar never rendered;
links past the 250th silently cut; a garbled model answer read as "no jobs".
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse

import httpx

from job_hunt.services.screen import DEFAULT_MODEL, _run_mmx
from job_hunt.services.web_extract import _is_proxy_only_host

EMPLOYERS_PATH = Path("data/designated-employers.tsv")
BATCH = 250  # links per model call; a longer page is read in several calls, never cut

_ANCHOR_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_SKIP_HREF_RE = re.compile(r"^(mailto:|tel:|javascript:)|\.(jpg|jpeg|png|gif|svg|css|js|ico)(\?|$)", re.I)
_RETRY_CODES = frozenset({403, 429})  # a bot edge, not an answer (see web_extract._TLS_BLOCK_CODES)

SYSTEM = """You read the links on an employer's careers page for one job seeker. Output
only JSON, exactly {"jobs": [numbers], "follow": [numbers]} — no prose."""

BRIEF = """THE CANDIDATE is an IT / data / software person: software development, AI and
LLM engineering, data analysis and pipelines, business or systems analysis, IT
analyst and systems administration, database work, reporting and BI, systems
integration. He has NO clinical, trades, teaching, finance-accounting or
facilities training.

Below are the numbered links found on the careers page of {employer} ({community}).

"jobs": the numbers of links that are ONE specific job posting whose work is
technical in the sense above. Judge by the link text. Borderline but genuinely
technical: include it, a human reads every posting. A menu item or department
page ("IT Services", "Technology") is not a posting.
"follow": at most 3 numbers of links that lead to this employer's list of open
positions (an ATS board, "current opportunities", "view all jobs"), if the page
itself does not list the postings. Never a news, policy or login page.

LINKS:
{links}
"""


@dataclass(frozen=True)
class Employer:
    employer: str
    community: str
    province: str
    careers_url: str


@dataclass(frozen=True)
class Link:
    url: str
    text: str


@dataclass(frozen=True)
class SweptRow:
    url: str
    title: str
    company: str
    location: str


@dataclass
class SweepResult:
    employer: Employer
    jobs: list[Link] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class SweepReport:
    rows: list[SweptRow]
    unreadable: list[SweepResult]  # employers that yielded nothing because a page or the model failed
    partial: list[SweepResult]  # employers that yielded postings, but a page or the model failed on the way


class ModelUnavailable(RuntimeError):
    pass


def load_employers(path: Path = EMPLOYERS_PATH) -> list[Employer]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            Employer(
                employer=(row.get("employer") or "").strip(),
                community=(row.get("community") or "").strip(),
                province=(row.get("province") or "").strip(),
                careers_url=(row.get("careers_url") or "").strip(),
            )
            for row in csv.DictReader(handle, delimiter="\t")
            if (row.get("employer") or "").strip()
        ]


def _page_links(html: str, base_url: str) -> list[Link]:
    """Every distinct http(s) anchor with visible text, in page order.

    A same-page ``#section`` link is skipped; a ``#/jobs/123`` route is a page of
    its own in a single-page ATS and is kept.
    """
    links: list[Link] = []
    seen: set[str] = set()
    for href, inner in _ANCHOR_RE.findall(html):
        href = unescape(href.strip())
        if _SKIP_HREF_RE.search(href) or (href.startswith("#") and not href.startswith("#/")):
            continue
        try:
            url = urljoin(base_url, href)
            scheme = urlparse(url).scheme
        except ValueError:  # "https://[broken" — one bad anchor, not the whole sweep (Codex round 4)
            continue
        if scheme not in {"http", "https"} or url in seen or _is_proxy_only_host(url):
            continue
        text = re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", inner))).strip()
        if not text:
            continue
        seen.add(url)
        links.append(Link(url=url, text=text[:160]))
    return links


def _parse_answer(answer: str, count: int) -> tuple[list[int], list[int]] | None:
    """The model's indices, kept only when they name a link that was offered.

    ``None`` when the answer is not the JSON asked for — an unreadable answer,
    not an answer of "no jobs".
    """
    # The first object that decodes, not first "{" to last "}": a stray brace after the
    # JSON made a correct answer unreadable (OpenCode round 4b)
    payload = None
    for match in re.finditer(r"\{", answer or ""):
        try:
            payload, _ = json.JSONDecoder().raw_decode(answer, match.start())
            break
        except ValueError:
            continue
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        return None  # `{}`, `{"error": ...}`, `{"jobs": "1"}` — not an answer (Codex red team round 2)
    if not isinstance(payload.get("follow", []), list):
        return None

    def indices(key: str) -> list[int]:
        values = payload.get(key) or []  # "follow" may be left out when there is nothing to follow
        return [int(v) for v in values if isinstance(v, (int, str)) and str(v).isdigit() and 1 <= int(v) <= count]

    return indices("jobs"), indices("follow")[:3]


MAX_REDIRECTS = 5
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class LinkedInRedirect(RuntimeError):
    pass


def _fetch(url: str, timeout: float = 30.0) -> tuple[str, str]:
    """(html, final_url). Raises on failure, so the caller can report it.

    Redirects are followed by hand, one hop at a time, so a hop to LinkedIn is
    refused *before* it is requested — following them automatically sent the
    request first and checked after (Codex red team round 2, 2026-10-24).
    """
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": _UA}) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            if _is_proxy_only_host(url):
                raise LinkedInRedirect("redirected to LinkedIn; not read")
            response = client.get(url)
            if response.is_redirect and response.headers.get("location"):
                url = urljoin(str(response.url), response.headers["location"])
                continue
            if response.status_code in _RETRY_CODES:
                # Some hosts refuse httpx's TLS handshake and answer curl (web_extract,
                # digitalnovascotia.com 2026-08-16). curl does not follow redirects here.
                html = _curl_once(url, timeout)
                if html:
                    return html, url
            response.raise_for_status()
            return response.text, str(response.url)
    raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=None)


def _curl_once(url: str, timeout: float) -> str:
    """One curl GET with no redirect following. "" when curl is missing or fails."""
    import subprocess

    if not shutil.which("curl"):
        return ""
    # -f: an error status is a failure, not a page — a 403 "Access denied" body
    # read as the careers page yielded zero jobs and no error (OpenCode round 2).
    proc = subprocess.run(["curl", "-sf", "--max-time", str(int(timeout)), "-A", _UA, url],
                          capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else ""


def _render(url: str) -> tuple[str, str]:
    """The page as a browser builds it, with every request to a LinkedIn host
    refused — subresources included (an embedded widget or tracking script)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.route(
                "**/*",
                lambda route: route.abort() if _is_proxy_only_host(route.request.url) else route.continue_(),
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            return page.content(), page.url
        finally:
            browser.close()


def _ask(employer: Employer, links: list[Link], *, model: str = DEFAULT_MODEL) -> tuple[list[int], list[int], str]:
    numbered = "\n".join(f"{i}. {link.text} -> {link.url}" for i, link in enumerate(links, start=1))
    prompt = BRIEF.format(employer=employer.employer, community=employer.community or employer.province,
                          links=numbered)
    answer, error = _run_mmx(prompt, model, 4000, 240, system=SYSTEM)
    if error:
        return [], [], error
    parsed = _parse_answer(answer, len(links))
    if parsed is None:
        return [], [], "model answer was not the JSON asked for"
    return parsed[0], parsed[1], ""


def _pick(employer: Employer, links: list[Link], ask: Callable[..., tuple[list[int], list[int], str]]
          ) -> tuple[list[Link], list[Link], str]:
    """(postings, follow-links, error) over every link, ``BATCH`` at a time."""
    jobs: list[Link] = []
    follow: list[Link] = []
    for start in range(0, len(links), BATCH):
        batch = links[start:start + BATCH]
        try:
            picked, onward, error = ask(employer, batch)
        except Exception as exc:  # noqa: BLE001 — keep the batches already picked (Codex round 4)
            picked, onward, error = [], [], f"{type(exc).__name__}: {str(exc)[:120]}"
        if error:
            return jobs, follow[:3], error
        jobs.extend(batch[i - 1] for i in picked if 1 <= i <= len(batch))
        follow.extend(batch[i - 1] for i in onward if 1 <= i <= len(batch))
    return jobs, follow[:3], ""


def _sweep_employer(
    employer: Employer,
    *,
    fetch: Callable[[str], tuple[str, str]],
    render: Callable[[str], tuple[str, str]] | None,
    ask: Callable[..., tuple[list[int], list[int], str]],
) -> SweepResult:
    """The careers page, and at most three pages one hop from it.

    A page is rendered in a browser when its served HTML yields nothing — no
    postings and nothing to follow — which covers both a bare ``<div id=app>``
    and a JavaScript job list under a server-rendered nav bar.
    """
    result = SweepResult(employer=employer)
    queue: list[tuple[str, int]] = [(employer.careers_url, 0)]
    visited: set[str] = set()

    def note(error: str) -> None:
        result.error = result.error or error

    while queue:
        url, depth = queue.pop(0)
        if url in visited or _is_proxy_only_host(url):
            continue
        visited.add(url)
        try:
            html, final_url = fetch(url)
        except LinkedInRedirect as exc:
            note(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 — reported per employer, never fatal
            note(f"{type(exc).__name__}: {str(exc)[:120]}")
            continue
        if _is_proxy_only_host(final_url):
            note("redirected to LinkedIn; not read")
            continue
        links = _page_links(html, final_url)
        jobs, follow, error = _pick(employer, links, ask)
        if not error and not jobs and not follow and render is not None:
            try:
                html, final_url = render(final_url)  # already resolved hop by hop; see _fetch
            except Exception as exc:  # noqa: BLE001
                note(f"render: {type(exc).__name__}")
            else:
                if _is_proxy_only_host(final_url):
                    note("redirected to LinkedIn; not read")
                    continue
                links = _page_links(html, final_url)
                jobs, follow, error = _pick(employer, links, ask)
        if not links and not error:
            # A board with no anchors even after rendering (Greater Sudbury's
            # PeopleSoft page) was reported as "nothing found" (OpenCode round 3).
            note("no links on the page, even rendered")
        if error:
            note(f"model: {error}")  # keep what the batches before the failure picked
        known = {job.url for job in result.jobs}
        result.jobs.extend(job for job in jobs if job.url not in known)
        if depth == 0:
            queue.extend((link.url, 1) for link in follow)
    return result


def location_of(employer: Employer, posting_url: str = "") -> str:
    """The employer's own community, unless the posting may be anywhere.

    A large company's own career site, or an enterprise ATS whose URL does not
    name the community: Verafin's careers page led to Nasdaq's Workday, and its
    two technical postings were in New York and Toronto, not St. John's
    (2026-10-24). Those are left blank for the reader to place. A regional
    employer on Workday (City of Vernon) names its town in the posting URL and
    keeps it.
    """
    from job_hunt.services.triage import ENTERPRISE_ATS_RE, LARGE_EMPLOYER_SITE_RE

    community = ", ".join(part for part in (employer.community, employer.province) if part)
    if not posting_url:
        return community
    if LARGE_EMPLOYER_SITE_RE.match(posting_url):
        return ""
    if ENTERPRISE_ATS_RE.match(posting_url):
        town = re.sub(r"[^a-z]+", "", employer.community.lower())
        # the path, not the host: a tenant named after the town is not a location (OpenCode round 4b)
        path = re.sub(r"[^a-z]+", "", unquote(urlparse(posting_url).path).lower())
        return community if town and town in path else ""
    return community


def as_path(url: str) -> str:
    """``https://x.ca/#/jobs/1`` -> ``https://x.ca/jobs/1``: a single-page ATS route
    judged as the page it is. The scan's posting-URL check read the bare root and
    discarded it (Codex red team round 2)."""
    parsed = urlparse(url)
    if parsed.fragment.startswith("/"):
        return parsed._replace(path=parsed.path.rstrip("/") + parsed.fragment, fragment="").geturl()
    return url


def collect(
    employers: list[Employer],
    *,
    known_urls: set[str],
    keep: Callable[[str, str], bool] = lambda _url, _title: True,
    fetch: Callable[[str], tuple[str, str]] = _fetch,
    render: Callable[[str], tuple[str, str]] | None = _render,
    ask: Callable[..., tuple[list[int], list[int], str]] | None = None,
    concurrency: int = 3,
) -> SweepReport:
    """New technical postings from these employers' careers pages.

    ``keep(url, title)`` is the caller's posting/title gate (the scan's
    ``is_job_posting_url`` and negative title filter); a row it refuses, or one
    already in ``known_urls``, is not returned. Employers with no careers URL
    are skipped. Concurrency is capped at 3 — mmx fails under more (AGENTS.md,
    ``email summarize``). Raises ``ModelUnavailable`` before fetching anything
    when the default model is not installed.
    """
    if ask is None:
        if not shutil.which("mmx"):
            raise ModelUnavailable("mmx not on PATH")
        ask = _ask

    def one(employer: Employer) -> SweepResult:
        try:
            return _sweep_employer(employer, fetch=fetch, render=render, ask=ask)
        except Exception as exc:  # noqa: BLE001 — one employer's crash never ends the sweep
            return SweepResult(employer=employer, error=f"{type(exc).__name__}: {str(exc)[:120]}")

    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, 3))) as pool:
        results = list(pool.map(one, [e for e in employers if e.careers_url]))

    seen = set(known_urls)
    rows: list[SweptRow] = []
    for result in results:
        for link in result.jobs:
            if link.url in seen or _is_proxy_only_host(link.url) or not keep(as_path(link.url), link.text):
                continue
            seen.add(link.url)
            # "|" is the pipeline's field separator; "Apply Now | IT Analyst" shifted
            # the row's fields and put the title in the location (OpenCode round 2).
            rows.append(SweptRow(url=link.url, title=link.text.replace("|", "-"),
                                 company=result.employer.employer.replace("|", "-"),
                                 location=location_of(result.employer, link.url)))
    return SweepReport(rows=rows, unreadable=[r for r in results if r.error and not r.jobs],
                       partial=[r for r in results if r.error and r.jobs])
