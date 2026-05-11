from __future__ import annotations

import csv
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, Field

from job_hunt.repositories.tracker_repo import TrackerRepository, normalize
from job_hunt.services.profile_loader import current_mode, discovery_context


class ScannedJob(BaseModel):
    url: str
    title: str
    company: str
    location: str = ""
    portal: str
    source: str
    status: str = "new"


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
        for job in jobs:
            if not _title_matches(job.title, positives, negatives):
                continue
            result.matched_jobs += 1
            if not include_non_canada and not _location_matches_canada(job.location):
                result.skipped_filtered += 1
                job.status = "skipped_location"
                if apply:
                    _append_scan_history(job)
                continue
            if job.url in known_urls or (normalize(job.company), normalize(job.title)) in known_company_roles:
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
            for job in channel_jobs:
                if not _title_matches(job.title, positives, negatives):
                    continue
                result.fetched_jobs += 1
                result.matched_jobs += 1
                if not include_non_canada and not _location_matches_canada(job.location):
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
                    known_company_roles.add(
                        (normalize(job.company), normalize(job.title))
                    )
                if apply:
                    _append_scan_history(job)

    if apply and result.jobs:
        _append_pipeline(result.jobs)
    return result


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

        for role in roles:
            for location in locations:
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
                            location="",
                            portal=cid,
                            source=query,
                        )
                    )
    return out


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
    return host in {"jobs.lever.co", "jobs.ashbyhq.com", "job-boards.greenhouse.io", "boards.greenhouse.io"}


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


def _title_matches(title: str, positives: list[str], negatives: list[str]) -> bool:
    title_lower = title.lower()
    if any(item and item in title_lower for item in negatives):
        return False
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
    ]
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
    return any(token in value for token in allowed_tokens) and not any(
        token in value for token in blocked_tokens
    )


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
        lines.append(f"- [ ] {job.url} | {job.company} | {job.title}{location} | source: {job.portal}")
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
