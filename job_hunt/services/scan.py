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
) -> ScanResult:
    if not config_path.exists():
        return ScanResult(errors=[f"{config_path} not found. Run `job-hunt init` or create tracked_companies."])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    positives = [str(item).lower() for item in config.get("title_filter", {}).get("positive", [])]
    negatives = [str(item).lower() for item in config.get("title_filter", {}).get("negative", [])]
    # P1-4 sub-phase 4b: include companies with `scan_method: websearch` when a
    # provider is wired in. Without a provider we still skip them (current behavior).
    def _eligible(item: dict[str, Any]) -> bool:
        if not item.get("enabled", True):
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
                jobs = scan_via_websearch(item, web_search_provider)
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

    if apply and result.jobs:
        _append_pipeline(result.jobs)
    return result


def scan_via_websearch(company: dict[str, Any], provider) -> list[ScannedJob]:
    """Tier-3 scan: WebSearch the company's `scan_query` and turn hits into ScannedJobs.

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
    company_name = company.get("name") or ""
    company_norm = normalize(company_name)
    hits = provider.search(query)
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
