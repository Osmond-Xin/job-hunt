from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, Field

from job_hunt.repositories.tracker_repo import TrackerRepository, normalize
from job_hunt.services.adzuna import scan_adzuna
from job_hunt.services.gov_boards import scan_gov_boards
from job_hunt.services.regional_boards import scan_regional_boards
from job_hunt.services.immigration import place_tokens as immigration_place_tokens
from job_hunt.services.jobbank import scan_jobbank
from job_hunt.services.workday_boards import scan_workday
from job_hunt.services.profile_loader import current_mode, discovery_context
from job_hunt.services.web_extract import _bamboohr_slug


class ScannedJob(BaseModel):
    url: str
    title: str
    company: str
    location: str = ""
    portal: str
    source: str
    status: str = "new"
    # Public-sector competitions close on a hard date and stop accepting
    # applications that day. The boards publish it; dropping it meant the
    # pipeline could not tell a posting with two days left from a fresh one.
    closes: str = ""
    # Freshness is one of the four axes triage ranks on, and it reads the date
    # off the pipeline line. Every board row carried a blank one, so the whole
    # direct-board corpus scored as undated and tied at the top.
    posted: str = ""


class ScanResult(BaseModel):
    scanned_companies: int = 0
    fetched_jobs: int = 0
    matched_jobs: int = 0
    skipped_filtered: int = 0
    new_jobs: int = 0
    skipped_duplicates: int = 0
    errors: list[str] = Field(default_factory=list)
    jobs: list[ScannedJob] = Field(default_factory=list)


def scan_portals(
    *,
    config_path: Path = Path("config/portals.yml"),
    company: str | None = None,
    limit_companies: int | None = None,
    apply: bool = False,
    include_non_canada: bool = False,
    web_search_provider=None,
    mode: str | None = None,
    discovery_channel: str | None = None,
    settings=None,
) -> ScanResult:
    """Run the multi-tier scan.

    Tier 1: per-company direct ATS fetch (Greenhouse / Lever / Ashby).
    Tier 2: per-company WebSearch with `scan_method: websearch`.
    Tier 3: cross-employer discovery channels (LinkedIn / Indeed / Glassdoor /
            student boards) — see ``scan_discovery_channels``. These run only
            when a ``web_search_provider`` is wired in and the channel is
            both ``enabled: true`` in portals.yml and matches the active mode.
            Pass ``discovery_channel="<id>"`` to restrict tier 3 to one
            channel.
    Tier 4: Job Bank direct search (``jobbank_direct``).
    Tier 5: public-sector boards — GNWT, Nova Scotia, New Brunswick,
        Manitoba (``gov_boards``), and regional tech-industry boards
        (``regional_boards``). Highest signal per request in this search:
        small local employers in the regions that carry immigration value,
        largely absent from the national aggregators.
    Tier 6: Workday CxS JSON boards (``workday_boards``).
    Tier 7: Adzuna aggregator API (``settings.adzuna`` + env credentials).

    Tiers 4-7 need no WebSearch provider and consume no search quota. They
    return structured employer / location fields, so they are strictly better
    per result than the tier-3 channels that cover the same boards.
    """
    if not config_path.exists():
        return ScanResult(errors=[f"{config_path} not found. Run `job-hunt init` or create tracked_companies."])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    active_mode = mode or current_mode()
    positives, negatives = _select_title_filter(config.get("title_filter") or {}, active_mode)
    # P1-4 sub-phase 4b: include companies with `scan_method: websearch` when a
    # provider is wired in. Without a provider we still skip them (current behavior).
    # Companies may also opt into a specific mode via `eligibility_tags`. Missing
    # tags = company is scanned in both modes (covers the common case where a
    # single board mixes intern + FT).
    def _eligible(item: dict[str, Any]) -> bool:
        if not item.get("enabled", True):
            return False
        if not _company_matches_mode(item, active_mode):
            return False
        if _supports_direct_fetch(item):
            return True
        if web_search_provider is not None and item.get("scan_method") == "websearch":
            return True
        return False

    companies = [item for item in config.get("tracked_companies", []) if _eligible(item)]
    if company:
        company_norm = normalize(company)
        companies = [item for item in companies if company_norm in normalize(item.get("name", ""))]
    if limit_companies is not None:
        companies = companies[:limit_companies]

    result = ScanResult(scanned_companies=len(companies))
    known_urls = _known_urls()
    known_company_roles = _known_company_roles()

    for item in companies:
        started = time.perf_counter()
        try:
            if _supports_direct_fetch(item):
                jobs = _fetch_company_jobs(item)
            elif web_search_provider is not None and item.get("scan_method") == "websearch":
                jobs = scan_via_websearch(item, web_search_provider, mode=active_mode)
            else:
                jobs = []
            latency_ms = int((time.perf_counter() - started) * 1000)
            _write_web_stat(item, True, latency_ms, len(jobs))
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            _write_web_stat(item, False, latency_ms, 0, str(exc))
            result.errors.append(f"{item.get('name', 'unknown')}: {exc}")
            continue

        result.fetched_jobs += len(jobs)
        _accept_jobs(
            jobs,
            result,
            positives=positives,
            negatives=negatives,
            include_non_canada=include_non_canada,
            known_urls=known_urls,
            known_company_roles=known_company_roles,
            apply=apply,
        )

    # Tier 3: cross-employer discovery channels (LinkedIn, Indeed, Glassdoor,
    # WaterlooWorks, TalentEgg, Magnet, ...). Only runs when a provider is
    # configured; respects per-channel `enabled` + `modes`. The channel ID
    # filter (--channel) is applied here so users can selectively burn quota.
    if web_search_provider is not None:
        channels_raw = config.get("discovery_channels") or []
        if not company and channels_raw:  # --company restricts to tracked entries
            channel_jobs = scan_discovery_channels(
                channels_raw,
                web_search_provider,
                mode=active_mode,
                channel_id=discovery_channel,
            )
            _accept_jobs(
                channel_jobs,
                result,
                positives=positives,
                negatives=negatives,
                include_non_canada=include_non_canada,
                known_urls=known_urls,
                known_company_roles=known_company_roles,
                apply=apply,
                count_fetched=True,
            )

    # Tier 4: Job Bank direct fetch. No WebSearch provider and no quota — the
    # board's own search is queried over NOC code x province. Runs whenever
    # `jobbank_direct.enabled` is set, including when tier 3 is off.
    # Tiers 4-7 are all direct, structured and quota-free. They are skipped
    # under --company, which scopes the run to one tracked employer.
    if not company:
        extra_tiers = [
            _jobbank_scanned_jobs(config.get("jobbank_direct")),
            _gov_board_scanned_jobs(config.get("gov_boards"), result.errors),
            _regional_board_scanned_jobs(config.get("regional_boards"), result.errors),
            _workday_scanned_jobs(config.get("workday_boards")),
            _adzuna_scanned_jobs(settings, discovery_context().get("roles", [])),
        ]
        # All four already establish the occupation before we see the title:
        # Job Bank is queried by NOC code, Adzuna rows are filtered on their
        # own `category` facet, and the gov / Workday boards are small curated
        # employer boards. Requiring a positive title match on top of that
        # discards ~half of them for naming variance alone.
        for tier_jobs in extra_tiers:
            _accept_jobs(
                tier_jobs,
                result,
                positives=positives,
                negatives=negatives,
                include_non_canada=include_non_canada,
                known_urls=known_urls,
                known_company_roles=known_company_roles,
                apply=apply,
                count_fetched=True,
                require_positive=False,
            )

    if apply and result.jobs:
        _append_pipeline(result.jobs)
    return result


def _accept_jobs(
    jobs: list[ScannedJob],
    result: ScanResult,
    *,
    positives: list[str],
    negatives: list[str],
    include_non_canada: bool,
    known_urls: set[str],
    known_company_roles: set[tuple[str, str]],
    apply: bool,
    count_fetched: bool = False,
    require_positive: bool = True,
) -> None:
    """Run fetched jobs through title / location / dedup and record them.

    Shared by all scan tiers. ``count_fetched`` exists because the per-company
    tiers already add ``len(jobs)`` to ``fetched_jobs`` up front, while the
    search-derived tiers count a job only once it clears the title filter.

    ``require_positive=False`` is for sources that already carry an occupation
    classification, where re-deriving the occupation from the title is both
    redundant and lossy. Measured: Job Bank rows are fetched *by NOC code*, so
    their occupation is established — yet the positive title list discarded 83
    of 172 of them, including "devops engineer", "data architect",
    "database analyst" and 25 rows titled "information technology (IT)
    analyst". Negatives still apply, so off-target occupations are screened.
    """
    for job in jobs:
        if not _title_matches(
            job.title, positives, negatives, require_positive=require_positive
        ):
            continue
        if count_fetched:
            result.fetched_jobs += 1
        result.matched_jobs += 1
        if not include_non_canada and not _passes_canada_filter(job.location):
            result.skipped_filtered += 1
            job.status = "skipped_location"
            if apply:
                _append_scan_history(job)
            continue
        if (
            job.url in known_urls
            or (normalize(job.company), normalize(job.title)) in known_company_roles
        ):
            result.skipped_duplicates += 1
            job.status = "skipped_duplicate"
        else:
            result.new_jobs += 1
            job.status = "new"
            result.jobs.append(job)
            known_urls.add(job.url)
            known_company_roles.add((normalize(job.company), normalize(job.title)))
        if apply:
            _append_scan_history(job)


def _board_coverage_warnings(stats: dict[str, dict[str, Any]]) -> list[str]:
    """Turn a board sweep's own numbers into operator-visible warnings.

    These adapters have always measured whether they read a board to the end;
    nothing ever read the measurement, so a board that returned a quarter of
    its postings looked exactly like a board having a quiet week. Digital Nova
    Scotia did that for a day: 30 of 120, no warning anywhere.
    """
    warnings: list[str] = []
    for board_id, board in sorted(stats.items()):
        collected = board.get("collected", 0)
        advertised = board.get("advertised")
        if board.get("errors"):
            warnings.append(
                f"{board_id}: {board['errors']} failed request(s) — "
                f"{collected} postings may be an undercount, not a quiet board"
            )
        if board.get("truncated"):
            short = f", board advertises {advertised}" if advertised else ""
            warnings.append(
                f"{board_id}: page budget ran out with rows still coming "
                f"({collected} collected{short}) — raise max_pages"
            )
    return warnings


def _regional_board_scanned_jobs(
    config: dict[str, Any] | None, warnings: list[str] | None = None
) -> list[ScannedJob]:
    """Tier 5b: regional tech-industry boards. Free, structured, local."""
    stats: dict[str, dict[str, Any]] = {}
    try:
        rows = scan_regional_boards(config, stats=stats)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"regional boards: sweep failed ({exc})")
        return []
    if warnings is not None:
        warnings.extend(_board_coverage_warnings(stats))
    return [
        ScannedJob(
            url=row.get("url", ""),
            title=row["title"],
            company=row.get("company") or "Unknown (see posting)",
            location=row.get("location", ""),
            portal=row.get("board", "regional"),
            source=row.get("board", "regional"),
            posted=row.get("posted", ""),
        )
        for row in rows
        if (row.get("title") or "").strip() and row.get("url")
    ]


def _gov_board_scanned_jobs(
    gov_config: dict[str, Any] | None, warnings: list[str] | None = None
) -> list[ScannedJob]:
    """Tier 5: public-sector boards (GNWT, Nova Scotia). Free, structured."""
    stats: dict[str, dict[str, Any]] = {}
    # Whole-organisation employers (health authorities) post overwhelmingly
    # clinical roles, and a substring whitelist could not tell "Systems Analyst"
    # from "Engineer 5th Class" without also dropping "Clinical Systems
    # Consultant". A model reads the titles instead; boards opt in with
    # `title_screen: true` and a failure keeps every row.
    from job_hunt.services.screen import screen_titles

    try:
        rows = scan_gov_boards(gov_config, stats=stats, title_screener=screen_titles)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"gov boards: sweep failed ({exc})")
        return []
    if warnings is not None:
        warnings.extend(_board_coverage_warnings(stats))
    return [
        ScannedJob(
            url=row.get("url", ""),
            title=row["title"],
            company=row.get("company") or "Unknown",
            location=row.get("location", ""),
            portal=row.get("board", "gov"),
            source=row.get("board", "gov"),
            closes=row.get("closes", ""),
            posted=row.get("posted", ""),
        )
        for row in rows
        if (row.get("title") or "").strip() and row.get("url")
    ]


def _workday_scanned_jobs(workday_config: dict[str, Any] | None) -> list[ScannedJob]:
    """Tier 6: Workday CxS JSON boards. Free, structured."""
    try:
        rows = scan_workday(workday_config)
    except Exception:
        return []
    return [
        ScannedJob(
            url=row.get("url", ""),
            title=row["title"],
            company=row.get("company") or "Unknown",
            location=row.get("location", ""),
            portal="workday",
            source=f"workday {row.get('company', '')}",
        )
        for row in rows
        if (row.get("title") or "").strip() and row.get("url")
    ]


def _adzuna_scanned_jobs(settings, roles: list[str]) -> list[ScannedJob]:
    """Tier 7: Adzuna aggregator API. Credentials come from the environment."""
    config = getattr(settings, "adzuna", None) if settings is not None else None
    if config is None or not getattr(config, "enabled", False):
        return []
    app_id = os.getenv(getattr(config, "app_id_env", "ADZUNA_APP_ID"), "").strip()
    app_key = os.getenv(getattr(config, "app_key_env", "ADZUNA_APP_KEY"), "").strip()
    if not app_id or not app_key:
        return []
    try:
        rows = scan_adzuna(config, roles, app_id=app_id, app_key=app_key)
    except Exception:
        return []
    return [
        ScannedJob(
            url=row.get("url", ""),
            title=row["title"],
            company=row.get("company") or "Unknown",
            location=row.get("location", ""),
            portal="adzuna",
            source=f"adzuna {row.get('query', '')}",
        )
        for row in rows
        if (row.get("title") or "").strip() and row.get("url")
    ]


def _jobbank_scanned_jobs(jobbank_config: dict[str, Any] | None) -> list[ScannedJob]:
    """Tier 4: Job Bank direct search rows mapped onto ``ScannedJob``."""
    try:
        rows = scan_jobbank(jobbank_config)
    except Exception:
        return []
    jobs: list[ScannedJob] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        jobs.append(
            ScannedJob(
                url=row.get("url", ""),
                title=title,
                company=(row.get("company") or "Unknown").strip() or "Unknown",
                location=row.get("location", ""),
                portal="jobbank",
                source=f"jobbank fn21={row.get('noc', '')}",
            )
        )
    return jobs


def scan_discovery_channels(
    channels_raw: list[dict[str, Any]],
    provider,
    *,
    mode: str | None = None,
    profile_path: Path | None = None,
    channel_id: str | None = None,
) -> list[ScannedJob]:
    """Tier-3 cross-employer discovery: LinkedIn / Indeed / Glassdoor / student boards.

    Each channel in ``portals.yml::discovery_channels`` declares a
    ``query_template`` with ``{role}`` / ``{location}`` placeholders. The
    template expands over ``profile.candidate.target_roles`` ×
    ``target_locations`` and each query is sent through the provider (which
    is normally the cached/counted ``CachingProvider``, so repeat queries
    skip the API).

    Skipped silently:
    - Channel ``enabled: false`` (the default — opt-in to avoid quota burn).
    - Channel ``modes`` does not include the active mode.
    - No ``target_roles`` configured in profile.yml (nothing to interpolate).

    Returns ``[]`` when the channels list is empty or no channel matches.
    Returned ``ScannedJob.portal`` carries the channel ID; ``source`` carries
    the original query for audit.
    """
    if not channels_raw:
        return []
    active_mode = mode or current_mode()
    ctx = discovery_context(profile_path)
    roles = ctx.get("roles", [])
    locations = ctx.get("locations") or ["Canada"]
    if not roles:
        return []

    out: list[ScannedJob] = []
    seen_urls: set[str] = set()

    for raw in channels_raw:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id") or "").strip().lower()
        if not cid:
            continue
        if channel_id and cid != channel_id.lower():
            continue
        if not raw.get("enabled", False):
            continue
        channel_modes = raw.get("modes") or []
        if channel_modes and active_mode not in [str(m).lower() for m in channel_modes]:
            continue
        template = str(raw.get("query_template") or "").strip()
        if not template:
            continue
        # Optional per-channel location list (e.g. immigration-priority small
        # towns) — falls back to the profile-wide target locations.
        channel_locations = [
            str(x).strip() for x in (raw.get("locations") or []) if str(x).strip()
        ] or locations

        for role in roles:
            for location in channel_locations:
                query = template.format(role=role, location=location).strip()
                if not query:
                    continue
                try:
                    hits = provider.search(query)
                except Exception:
                    continue
                for hit in hits:
                    url = (getattr(hit, "url", "") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if not is_job_posting_url(url):
                        continue
                    title_line = (
                        getattr(hit, "title", "") or getattr(hit, "description", "") or ""
                    )
                    parsed_title, parsed_company = parse_search_result_title(title_line)
                    title = (parsed_title or getattr(hit, "title", "") or "").strip()
                    if not title:
                        continue
                    company_name = (parsed_company or "Unknown").strip() or "Unknown"
                    out.append(
                        ScannedJob(
                            url=url,
                            title=title,
                            company=company_name,
                            # Stamp the queried location so the Canada / priority
                            # filter sees the real region instead of "" (dropped).
                            location=location,
                            portal=cid,
                            source=query,
                        )
                    )
    return out


# ---------------------------------------------------------------------------
# Job-posting URL filter for search-derived hits (added 2026-08-06).
#
# Measured on a full national scan: of 294 jobbank.gc.ca hits, only 10 were
# real postings — 241 were /marketreport/ occupation-and-wage pages, 22 were
# search-result pages, 17 were outlook reports. Indeed contributed category
# pages ("$46K-$214K Compute & Storage Owner Jobs"), and 17 hits came from
# uk./in. LinkedIn. A search engine cannot be asked for "only detail pages",
# so the shape of the URL is the filter.
#
# Three-way decision, deliberately not a pure allowlist: tier-2 company
# queries legitimately land on arbitrary employer career domains, and those
# must keep flowing.
#   1. Host is a known job board  -> require that board's posting-URL shape.
#   2. Host is known non-job noise -> reject.
#   3. Anything else (company career sites) -> allow.
# ---------------------------------------------------------------------------

# host suffix -> path fragments that mark an individual posting
_JOB_BOARD_POSTING_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("jobbank.gc.ca", ("/jobsearch/jobposting/",)),
    ("indeed.com", ("/viewjob", "/rc/clk", "/job/")),
    ("linkedin.com", ("/jobs/view/",)),
    ("glassdoor.com", ("/job-listing/", "/partner/jobListing")),
    ("glassdoor.ca", ("/job-listing/", "/partner/jobListing")),
    ("ziprecruiter.com", ("/jobs/", "/c/")),
    ("wellfound.com", ("/jobs/", "/l/")),
    ("dice.com", ("/job-detail/", "/jobs/detail/")),
    ("talent.com", ("/view",)),
    ("monster.ca", ("/job-openings/",)),
    ("workopolis.com", ("/jobsearch/viewjob/", "/job/")),
    ("simplyhired.ca", ("/job/",)),
    ("theladders.com", ("/job/",)),
)

# Path fragments that mark a marketing / reference page on an otherwise
# unknown host. Keeps company career domains flowing while dropping the
# FAQ, product and blog pages that rank for role keywords.
_NON_POSTING_PATH_FRAGMENTS: tuple[str, ...] = (
    "/faq",
    "/blog/",
    "/pricing",
    "/about-us",
    "/product/",
    "/products/",
    "/glossary",
    "/salary-guide",
    "/news-release",
    "-salary-",
)

# Observed in real scan output as non-job results. Reference sites, salary
# aggregators, forums and stock-analysis pages that rank for role keywords.
_NON_JOB_HOSTS: frozenset[str] = frozenset(
    {
        "wikipedia.org",
        "levels.fyi",
        "github.com",
        "teamblind.com",
        "simplywall.st",
        "tipranks.com",
        "salaryexpert.com",
        "payscale.com",
        "reddit.com",
        "youtube.com",
        "medium.com",
        "coursera.org",
        "udemy.com",
        # Press-release wires, data brokers and software-review sites: they
        # rank for company + role keywords but never carry a posting.
        "globenewswire.com",
        "prnewswire.com",
        "businesswire.com",
        "rocketreach.co",
        "zoominfo.com",
        "crunchbase.com",
        "research.com",
        "g2.com",
        "capterra.com",
    }
)

# LinkedIn country subdomains whose postings are not Canadian roles. The
# bare domain and ca./www. are kept.
_ALLOWED_LINKEDIN_HOSTS: frozenset[str] = frozenset(
    {"linkedin.com", "www.linkedin.com", "ca.linkedin.com"}
)


def is_job_posting_url(url: str) -> bool:
    """True when ``url`` looks like an individual job posting worth importing.

    See the module comment above for the measurement that motivated this.
    Unknown hosts are allowed — the goal is to strip reference pages off the
    known boards, not to maintain a registry of every employer domain.
    """
    parsed = urlparse((url or "").strip())
    host = (parsed.netloc or "").lower().split(":")[0]
    if not host or parsed.scheme not in ("http", "https"):
        return False
    path = (parsed.path or "") + ("?" + parsed.query if parsed.query else "")

    bare = host[4:] if host.startswith("www.") else host
    if any(bare == n or bare.endswith("." + n) for n in _NON_JOB_HOSTS):
        return False

    for suffix, fragments in _JOB_BOARD_POSTING_PATTERNS:
        if host == suffix or host.endswith("." + suffix):
            if suffix == "linkedin.com" and host not in _ALLOWED_LINKEDIN_HOSTS:
                return False
            return any(frag in path for frag in fragments)

    # Unknown host. A site root is never a posting, and neither are the
    # marketing pages that rank for the same keywords.
    if (parsed.path or "/").rstrip("/") == "":
        return False
    lowered = path.lower()
    return not any(frag in lowered for frag in _NON_POSTING_PATH_FRAGMENTS)


def _passes_canada_filter(location: str) -> bool:
    """Keep a job unless it has a location that is clearly non-Canadian.

    WebSearch / discovery-channel hits carry no parsed location (``""``). An
    unknown location must NOT be treated as non-Canadian — the queries are
    already Canada/location-scoped — otherwise the entire tier-2/3 discovery
    stream is silently dropped in default (Canada-only) mode.
    """
    return not location or _location_matches_canada(location)


def scan_via_websearch(
    company: dict[str, Any],
    provider,
    *,
    mode: str | None = None,
) -> list[ScannedJob]:
    """Tier-3 scan: WebSearch the company's `scan_query` and turn hits into ScannedJobs.

    Under student mode the configured ``scan_query`` is automatically augmented
    with intern / co-op / new-grad terms so the provider returns student-eligible
    hits even when the original query was written for a full-time hunt. The
    title filter still gates results downstream — the augmentation only widens
    Brave's recall, not the final acceptance set.

    Each hit's title+description first line is parsed with
    ``parse_search_result_title`` to recover (job-title, company-name). The
    company name from the hit must roughly match the configured company —
    otherwise we keep the configured name to avoid false-positive imports
    from sites that aggregate jobs across multiple employers.

    Returns ``[]`` when the company has no `scan_query` or the provider
    yields no usable hits.
    """
    query = company.get("scan_query") or ""
    if not query.strip():
        return []
    active_mode = mode or current_mode()
    augmented_query = _augment_query_for_mode(query, active_mode)
    company_name = company.get("name") or ""
    company_norm = normalize(company_name)
    hits = provider.search(augmented_query)
    jobs: list[ScannedJob] = []
    seen_urls: set[str] = set()
    for hit in hits:
        url = (hit.url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        if not is_job_posting_url(url):
            continue
        title_line = hit.title or hit.description or ""
        parsed_title, parsed_company = parse_search_result_title(title_line)
        # Prefer parsed title; fall back to whole title line. Reject empty.
        title = (parsed_title or hit.title or "").strip()
        if not title:
            continue
        # Only trust the parsed company when it roughly matches the configured one
        # (defends against aggregator results bleeding in).
        resolved_company = company_name
        if parsed_company and normalize(parsed_company) == company_norm:
            resolved_company = parsed_company
        jobs.append(
            ScannedJob(
                url=url,
                title=title,
                company=resolved_company,
                location="",  # WebSearch snippets rarely carry structured location
                portal="websearch",
                source=company_name,
            )
        )
    return jobs


def _supports_direct_fetch(company: dict[str, Any]) -> bool:
    if company.get("api"):
        return True
    url = company.get("careers_url", "")
    host = urlparse(url).netloc
    if host in {"jobs.lever.co", "jobs.ashbyhq.com", "job-boards.greenhouse.io", "boards.greenhouse.io"}:
        return True
    # BambooHR gives every employer its own subdomain, so this one is a suffix
    # test rather than a fixed host. Added 2026-09-01: Vendasta and Hiveway —
    # the only two employers that have produced a real human interview — both
    # post here, and neither was reachable by any tier of the scan.
    return _bamboohr_slug(host) != ""


def _fetch_company_jobs(company: dict[str, Any]) -> list[ScannedJob]:
    api = company.get("api") or _infer_api_url(company.get("careers_url", ""))
    if not api:
        return []
    host = urlparse(api).netloc
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "job-hunt/0.1"}) as client:
        response = client.get(api)
        response.raise_for_status()
        raw = response.json()
    if "greenhouse.io" in host:
        return _parse_greenhouse(raw, company)
    if "lever.co" in host:
        return _parse_lever(raw, company)
    if "ashbyhq.com" in host:
        return _parse_ashby(raw, company)
    if _bamboohr_slug(host):
        return _parse_bamboohr(raw, company, host)
    return []


def _infer_api_url(careers_url: str) -> str:
    parsed = urlparse(careers_url)
    slug = parsed.path.strip("/").split("/")[0]
    if parsed.netloc in {"job-boards.greenhouse.io", "boards.greenhouse.io"} and slug:
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    if parsed.netloc == "jobs.lever.co" and slug:
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    if parsed.netloc == "jobs.ashbyhq.com" and slug:
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    if _bamboohr_slug(parsed.netloc):
        # The board and its JSON live on the same host: /careers is the page a
        # human reads, /careers/list is the feed behind it.
        return f"https://{parsed.netloc}/careers/list"
    return ""


def _parse_greenhouse(raw: dict[str, Any], company: dict[str, Any]) -> list[ScannedJob]:
    jobs = raw.get("jobs") or []
    return [
        ScannedJob(
            url=item.get("absolute_url") or "",
            title=item.get("title") or "",
            company=company.get("name") or "",
            location=(item.get("location") or {}).get("name") or "",
            portal="greenhouse",
            source=company.get("name") or "",
        )
        for item in jobs
        if item.get("title") and item.get("absolute_url")
    ]


def _parse_lever(raw: list[dict[str, Any]], company: dict[str, Any]) -> list[ScannedJob]:
    parsed: list[ScannedJob] = []
    for item in raw:
        categories = item.get("categories") or {}
        parsed.append(
            ScannedJob(
                url=item.get("hostedUrl") or item.get("applyUrl") or "",
                title=item.get("text") or "",
                company=company.get("name") or "",
                location=categories.get("location") or "",
                portal="lever",
                source=company.get("name") or "",
            )
        )
    return [job for job in parsed if job.url and job.title]


def _parse_ashby(raw: dict[str, Any], company: dict[str, Any]) -> list[ScannedJob]:
    jobs = raw.get("jobs") or []
    parsed: list[ScannedJob] = []
    for item in jobs:
        location = item.get("location")
        if isinstance(location, dict):
            location = location.get("name")
        parsed.append(
            ScannedJob(
                url=item.get("jobUrl") or item.get("applyUrl") or "",
                title=item.get("title") or "",
                company=company.get("name") or "",
                location=location or "",
                portal="ashby",
                source=company.get("name") or "",
            )
        )
    return [job for job in parsed if job.url and job.title]


def _parse_bamboohr(raw: dict[str, Any], company: dict[str, Any], host: str) -> list[ScannedJob]:
    """Parse `https://<slug>.bamboohr.com/careers/list`.

    The feed carries no posting URL — only an id — so the link is rebuilt as
    `/careers/<id>`, which is the page a human applies through (verified
    against the two applications already on file: vendasta 811, hiveway 32).

    Location arrives as `{city, state}` with the province spelled out
    ("Saskatoon, Saskatchewan"), which is the form `_passes_canada_filter`
    already understands — so the Chennai and Boca Raton rows on Vendasta's own
    board are dropped downstream without special handling here.
    """
    parsed: list[ScannedJob] = []
    for item in raw.get("result") or []:
        job_id = str(item.get("id") or "").strip()
        title = (item.get("jobOpeningName") or "").strip()
        if not job_id or not title:
            continue
        location = item.get("location")
        if not isinstance(location, dict):
            location = {}
        # `atsLocation` is the newer field and is all-null on every board
        # measured so far; read it only when `location` has nothing.
        ats = item.get("atsLocation") if isinstance(item.get("atsLocation"), dict) else {}
        city = location.get("city") or ats.get("city") or ""
        region = location.get("state") or ats.get("province") or ats.get("state") or ""
        parsed.append(
            ScannedJob(
                url=f"https://{host}/careers/{job_id}",
                title=title,
                company=company.get("name") or "",
                location=", ".join(part for part in (city, region) if part),
                portal="bamboohr",
                source=company.get("name") or "",
            )
        )
    return parsed


# Student-mode augmentation appended to every `scan_query` when the active
# mode is "student". Brave / Google interpret the trailing parenthesised
# OR group as an additional AND constraint on the existing query, so a
# query like `site:shopify.com/careers "Data Analyst"` becomes
# `site:shopify.com/careers "Data Analyst" ("Intern" OR "Co-op" OR ...)` —
# narrows recall to student-eligible postings without dropping the site /
# role constraints. Empty in full mode (no augmentation; legacy behaviour).
_STUDENT_QUERY_TERMS = (
    '"Intern"',
    '"Internship"',
    '"Co-op"',
    '"Coop"',
    '"Co-operative"',
    '"New Grad"',
    '"Junior"',
    '"Student"',
)


def _augment_query_for_mode(query: str, mode: str) -> str:
    """Return ``query`` augmented with student-mode terms when applicable.

    Only the ``"student"`` mode appends a constraint. ``"full"`` and any
    other value pass the query through unchanged so existing FT scans behave
    exactly as before.
    """
    if mode != "student":
        return query
    base = query.rstrip()
    if not base:
        return base
    constraint = " OR ".join(_STUDENT_QUERY_TERMS)
    return f"{base} ({constraint})"


# Mode-specific tags for tracked companies. A company with `eligibility_tags`
# containing any STUDENT_TAG appears in student-mode scans; any FULL_TAG appears
# in full-mode scans. Missing or empty `eligibility_tags` = company scanned in
# both modes (default; matches the common case where one ATS board mixes
# intern + FT postings).
_STUDENT_TAGS = {"intern", "coop", "co-op", "student", "internship"}
_FULL_TAGS = {"full", "full_time", "fulltime", "ft"}


def _select_title_filter(raw: dict[str, Any], mode: str) -> tuple[list[str], list[str]]:
    """Return (positives, negatives) for the active mode.

    Prefers ``raw[<mode>].positive`` / ``raw[<mode>].negative``. Falls back to
    legacy top-level ``raw.positive`` / ``raw.negative`` when the mode group is
    absent — keeps older portals.yml files working without migration.
    """
    group = raw.get(mode)
    if isinstance(group, dict) and ("positive" in group or "negative" in group):
        positives = [str(item).lower() for item in (group.get("positive") or [])]
        negatives = [str(item).lower() for item in (group.get("negative") or [])]
        return positives, negatives
    positives = [str(item).lower() for item in (raw.get("positive") or [])]
    negatives = [str(item).lower() for item in (raw.get("negative") or [])]
    return positives, negatives


def _company_matches_mode(item: dict[str, Any], mode: str) -> bool:
    """True when this tracked-companies entry should be scanned under ``mode``.

    Missing / empty ``eligibility_tags`` means the company is mode-agnostic
    (scanned in both). When tags are present, the company must declare a tag
    aligned with the active mode to be included.
    """
    raw_tags = item.get("eligibility_tags")
    if not raw_tags:
        return True
    tags = {str(tag).strip().lower() for tag in raw_tags if str(tag).strip()}
    if not tags:
        return True
    if mode == "student":
        return bool(tags & _STUDENT_TAGS)
    return bool(tags & _FULL_TAGS)


def _title_matches(
    title: str,
    positives: list[str],
    negatives: list[str],
    *,
    require_positive: bool = True,
) -> bool:
    """Title gate.

    ``require_positive=False`` switches to negative-only screening, for
    sources that already establish the occupation by other means — see
    ``_accept_jobs``. Negatives always apply.
    """
    title_lower = title.lower()
    if any(item and item in title_lower for item in negatives):
        return False
    if not require_positive:
        return True
    return any(item and item in title_lower for item in positives)


def _location_matches_canada(location: str) -> bool:
    """Return true for locations that look viable for Canadian work authorization."""
    value = _compact_location(location)
    if not value:
        return False
    allowed_tokens = [
        "canada",
        "remote canada",
        "canadian",
        "toronto",
        "kitchener",
        "waterloo",
        "ottawa",
        "halifax",
        "bedford",
        "dartmouth",
        "nova scotia",
        "ontario",
        "calgary",
        "edmonton",
        "alberta",
        "vancouver",
        "british columbia",
        "montreal",
        "montréal",
        "quebec",
        "québec",
        # Full-Canada coverage: every province/territory + capitals. Smaller
        # towns come from profile immigration_priority place tokens below.
        "new brunswick",
        "moncton",
        "fredericton",
        "prince edward island",
        "charlottetown",
        "newfoundland",
        "st. john's",
        "manitoba",
        "winnipeg",
        "saskatchewan",
        "saskatoon",
        "regina",
        "yukon",
        "whitehorse",
        "northwest territories",
        "yellowknife",
        "nunavut",
        "iqaluit",
        # Continental postings. Canada is in North America, and the
        # forward-deployed lane advertises itself this way more often than not
        # ("Remote NORAM", "Remote - North America"). Discovery exists for
        # recall; a US-only role that slips through is caught when the JD is
        # fetched and scored, whereas one dropped here is never seen again.
        "north america",
        "noram",
        "americas",
    ]
    allowed_tokens += immigration_place_tokens()
    blocked_tokens = [
        "united states",
        "remote us",
        "remote u.s",
        "san francisco",
        "new york",
        "washington",
        "london",
        "united kingdom",
        "singapore",
        "korea",
        "japan",
        "poland",
        "spain",
        "uk",
        "us only",
    ]
    # Word-boundary matching so short tokens don't hit inside words
    # ("uk" must not match "yukon").
    blocked = any(
        re.search(rf"\b{re.escape(token)}\b", value) for token in blocked_tokens
    )
    if not any(token in value for token in allowed_tokens):
        return False
    # A posting that names Canada is open to Canada, whatever else it names.
    # "Remote US/Canada", "Remote, North America" and "New York or Remote
    # (Canada)" were all being dropped because a blocked token appeared beside
    # the Canadian one — and that is how most forward-deployed roles are
    # advertised. Only an explicit Canadian signal overrides the block; a bare
    # "remote" still does not.
    if _names_canada_explicitly(value):
        return True
    return not blocked


_EXPLICIT_CANADA_RE = re.compile(
    r"\b(canada|canadian|ontario|quebec|qu[eé]bec|alberta|manitoba|saskatchewan|yukon|nunavut|"
    r"british columbia|nova scotia|new brunswick|newfoundland|prince edward island|"
    r"northwest territories|toronto|montr[eé]al|vancouver|ottawa|calgary|edmonton|winnipeg|"
    r"halifax|saskatoon|regina|yellowknife|whitehorse|iqaluit|fredericton|moncton|charlottetown|"
    r"waterloo|kitchener)\b"
)


def _names_canada_explicitly(value: str) -> bool:
    """True when the location names Canada or a Canadian place, not just 'remote'."""
    return bool(_EXPLICIT_CANADA_RE.search(value))


def _compact_location(location: str) -> str:
    return re.sub(r"\s+", " ", location.lower().replace(";", " ")).strip()


# --- Search-result parsing (P2-10) ---
#
# Used to extract (title, company) from WebSearch result snippets that look
# like "Senior AI PM @ EverAI" or "Data Scientist | Acme" or
# "Software Engineer at Stripe".
_SEARCH_RESULT_RE = re.compile(
    r"^\s*(.+?)(?:\s*[@|—–\-]\s*|\s+at\s+)(.+?)\s*$",
    re.IGNORECASE,
)


def parse_search_result_title(text: str) -> tuple[str | None, str | None]:
    """Extract (title, company) from a WebSearch snippet's leading title line.

    Returns (None, None) when the line has no recognizable separator. Conservative
    by design — returns parsed values only when both halves are non-empty after
    stripping whitespace.
    """
    if not text:
        return None, None
    first_line = text.strip().splitlines()[0]
    match = _SEARCH_RESULT_RE.match(first_line)
    if not match:
        return None, None
    title = match.group(1).strip()
    company = match.group(2).strip()
    return (title or None), (company or None)



def _known_urls() -> set[str]:
    urls: set[str] = set()
    for path in [Path("data/scan-history.tsv"), Path("data/pipeline.md"), Path("data/applications.md")]:
        if path.exists():
            urls.update(re.findall(r"https?://[^\s)|]+", path.read_text(encoding="utf-8")))
    return urls


def _known_company_roles() -> set[tuple[str, str]]:
    return {
        (normalize(entry.company), normalize(entry.role))
        for entry in TrackerRepository(Path("data/applications.md")).parse()
    }


def _append_scan_history(job: ScannedJob) -> None:
    path = Path("data/scan-history.tsv")
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        if new_file:
            writer.writerow(["url", "first_seen", "portal", "title", "company", "status"])
        writer.writerow([job.url, dt.date.today().isoformat(), job.portal, job.title, job.company, job.status])


def _append_pipeline(jobs: list[ScannedJob]) -> None:
    path = Path("data/pipeline.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Pipeline — Job Inbox\n\n## Pending\n", encoding="utf-8")
    lines = ["", f"### Direct ATS Scan — {dt.date.today().isoformat()}", ""]
    for job in jobs:
        location = f" | {job.location}" if job.location else ""
        closes = f" | closes: {job.closes}" if job.closes else ""
        posted = f" | posted {job.posted}" if job.posted else ""
        lines.append(
            f"- [ ] {job.url} | {job.company} | {job.title}{location} "
            f"| source: {job.portal}{posted}{closes}"
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _write_web_stat(
    company: dict[str, Any],
    success: bool,
    latency_ms: int,
    content_count: int,
    error: str = "",
) -> None:
    path = Path("data/web-adapter-stats.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "graph_name": "scan_portals_graph",
        "node_name": "direct_ats_scan",
        "adapter": "direct_http",
        "company": company.get("name", ""),
        "url_host": urlparse(company.get("api") or company.get("careers_url", "")).netloc,
        "latency_ms": latency_ms,
        "success": success,
        "content_count": content_count,
        "error": error,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
