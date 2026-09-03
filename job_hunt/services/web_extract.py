from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel


class WebExtractResult(BaseModel):
    url: str
    text: str
    adapter: Literal["ats_api", "http_extract", "playwright_browser", "local_file"]
    title: str = ""
    company: str = ""
    location: str = ""
    ats: str = ""


async def extract_url_text(url: str, *, min_chars: int = 200) -> WebExtractResult:
    # P2-10: `local:jds/foo.md` short-circuits to a local read. No HTTP, no Playwright.
    # Useful for off-line evaluation, screenshots saved as text, or pasted JDs.
    if url.startswith("local:"):
        return _extract_local_file(url)

    _guard_proxy_only_host(url)

    ats_result = await _try_ats_api(url)
    if ats_result and len(ats_result.text) >= min_chars:
        return ats_result

    http_result = await _http_extract(url)
    if len(http_result.text) >= min_chars:
        return http_result

    playwright_result = await _try_playwright_extract(url)
    if playwright_result and len(playwright_result.text) > len(http_result.text):
        return playwright_result
    return http_result


def _extract_local_file(url: str) -> WebExtractResult:
    """Read ``local:<path>`` (relative or absolute) and return its text."""
    raw = url.removeprefix("local:").strip()
    path = Path(raw).expanduser()
    if not path.is_absolute() and not path.exists():
        # Try resolving against jds/, the local convention for pasted job descriptions.
        candidate = Path("jds") / raw
        if candidate.exists():
            path = candidate
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    title = ""
    if text:
        first_line = text.splitlines()[0].strip()
        if first_line.startswith("#"):
            title = first_line.lstrip("#").strip()
        else:
            title = first_line[:120]
    return WebExtractResult(
        url=url,
        text=clean_web_text(text),
        adapter="local_file",
        title=title,
    )


async def _try_ats_api(url: str) -> WebExtractResult | None:
    greenhouse = _greenhouse_api_url(url)
    if greenhouse:
        async with _client() as client:
            response = await client.get(greenhouse)
            if response.status_code < 400:
                raw = response.json()
                return WebExtractResult(
                    url=url,
                    text=clean_web_text(
                        "\n\n".join(
                            [
                                raw.get("title") or "",
                                (raw.get("location") or {}).get("name") or "",
                                raw.get("content") or "",
                            ]
                        )
                    ),
                    adapter="ats_api",
                    title=raw.get("title") or "",
                    company=_company_from_ats_url(url),
                    location=(raw.get("location") or {}).get("name") or "",
                    ats="greenhouse",
                )

    lever = _lever_api_url(url)
    if lever:
        async with _client() as client:
            response = await client.get(lever)
            if response.status_code < 400:
                raw = response.json()
                categories = raw.get("categories") or {}
                lists = raw.get("lists") or []
                description = "\n\n".join(
                    clean_web_text(item.get("content") or "") for item in lists if item.get("content")
                )
                return WebExtractResult(
                    url=url,
                    text=clean_web_text(
                        "\n\n".join(
                            [
                                raw.get("text") or "",
                                categories.get("team") or "",
                                categories.get("location") or "",
                                raw.get("descriptionPlain") or raw.get("description") or "",
                                description,
                            ]
                        )
                    ),
                    adapter="ats_api",
                    title=raw.get("text") or "",
                    company=_company_from_ats_url(url),
                    location=categories.get("location") or "",
                    ats="lever",
                )

    ashby = _ashby_board_api_url(url)
    if ashby:
        async with _client() as client:
            response = await client.get(ashby)
            if response.status_code < 400:
                raw = response.json()
                job = _find_ashby_job(raw, url)
                if job:
                    location = job.get("location")
                    if isinstance(location, dict):
                        location = location.get("name")
                    return WebExtractResult(
                        url=url,
                        text=clean_web_text(
                            "\n\n".join(
                                [
                                    job.get("title") or "",
                                    location or "",
                                    job.get("descriptionPlain") or job.get("descriptionHtml") or "",
                                ]
                            )
                        ),
                        adapter="ats_api",
                        title=job.get("title") or "",
                        company=_company_from_ats_url(url),
                        location=location or "",
                        ats="ashby",
                    )

    bamboo = _bamboohr_detail_api_url(url)
    if bamboo:
        async with _client() as client:
            response = await client.get(bamboo)
            if response.status_code < 400:
                job = ((response.json() or {}).get("result") or {}).get("jobOpening") or {}
                location = job.get("location")
                if not isinstance(location, dict):
                    location = {}
                where = ", ".join(
                    part for part in (location.get("city"), location.get("state")) if part
                )
                if job.get("jobOpeningName"):
                    return WebExtractResult(
                        url=url,
                        text=clean_web_text(
                            "\n\n".join(
                                [job.get("jobOpeningName") or "", where, job.get("description") or ""]
                            )
                        ),
                        adapter="ats_api",
                        title=job.get("jobOpeningName") or "",
                        company=_bamboohr_company(url),
                        location=where,
                        ats="bamboohr",
                    )

    return None


async def _http_extract(url: str) -> WebExtractResult:
    proxy = scrape_proxy() if _is_proxy_only_host(url) else ""
    try:
        async with _client(proxy=proxy) as client:
            response = await client.get(url)
            response.raise_for_status()
        html = response.text
        final_url = str(response.url)
    except httpx.HTTPStatusError as exc:
        # Some hosts fingerprint the TLS handshake rather than the User-Agent,
        # and httpx's ClientHello is not a browser's. Measured 2026-08-16 on
        # digitalnovascotia.com: httpx gets 403 with no headers, with a Chrome
        # UA, and with a full Chrome header set alike, while plain `curl` gets
        # 200 on the same URL over the same HTTP version. That cost a whole
        # batch of Halifax postings, so fall back to curl before giving up.
        if exc.response.status_code not in _TLS_BLOCK_CODES:
            raise
        html = _curl_get(url, proxy=proxy)
        if not html:
            raise
        final_url = url
    title = extract_html_title(html)
    body = extract_html_body(html)
    return WebExtractResult(
        url=final_url,
        text=clean_web_text("\n\n".join(part for part in [title, body] if part)),
        adapter="http_extract",
        title=title,
    )


async def _try_playwright_extract(url: str) -> WebExtractResult | None:
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None

    proxy = scrape_proxy() if _is_proxy_only_host(url) else ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, **({"proxy": {"server": proxy}} if proxy else {})
            )
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)
            title = await page.title()
            text = await page.locator("body").inner_text(timeout=10000)
            await browser.close()
    except Exception:
        return None
    cleaned = clean_web_text("\n\n".join(part for part in [title, text] if part))
    return WebExtractResult(
        url=url,
        text=cleaned,
        adapter="playwright_browser",
        title=title,
        company=_workday_company_from_url(url),
        location=_workday_location_from_text(cleaned),
        ats="workday" if _is_workday_url(url) else "",
    )


# Status codes worth a second attempt through curl. A 404 is an answer; a 403
# or a 429 from a bot-protection edge is not.
_TLS_BLOCK_CODES = frozenset({403, 429})

# Hosts this module must never fetch from the operator's own address.
#
# LinkedIn bans the *account* whose network it associates with scraping, not
# just the request, and the operator's account is the one his applications and
# outreach run through — losing it costs far more than any single JD is worth.
# `link_check` has refused to fetch linkedin.com since it was written; this
# module did not, so `evaluate <a linkedin URL>` still reached out directly.
# Closed 2026-08-16 on the operator's instruction.
#
# Note this is about *fetching a posting page*. Discovery's `site:linkedin.com`
# queries are answered out of the search provider's index and never touch
# LinkedIn, so they are unaffected.
_PROXY_ONLY_HOSTS = ("linkedin.com",)
_PROXY_ENV_VAR = "JOB_HUNT_SCRAPE_PROXY"


def scrape_proxy() -> str:
    """The configured egress proxy for hosts that must not see our own IP.

    Environment first so a one-off run can override, then `network.scrape_proxy`
    in profile.yml. Must be a proxy endpoint httpx/curl/Playwright can speak:
    `socks5://host:port` or `http://host:port`. A Shadowsocks subscription URL
    is not one — a local client has to terminate it and expose a port.
    """
    import os

    from_env = (os.environ.get(_PROXY_ENV_VAR) or "").strip()
    return from_env or _proxy_from_profile()


def _proxy_from_profile() -> str:
    """`network.scrape_proxy` from profile.yml, or "" if absent/unreadable.

    Split out so a test can isolate the guard from whatever the operator
    happens to have configured locally.
    """
    try:
        import yaml

        raw = yaml.safe_load(Path("profile/profile.yml").read_text(encoding="utf-8")) or {}
        network = raw.get("network") if isinstance(raw, dict) else None
        if isinstance(network, dict):
            return str(network.get("scrape_proxy") or "").strip()
    except Exception:
        pass
    return ""


def _is_proxy_only_host(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    bare = host[4:] if host.startswith("www.") else host
    return any(bare == h or bare.endswith("." + h) for h in _PROXY_ONLY_HOSTS)


def _guard_proxy_only_host(url: str) -> None:
    """Refuse to fetch a proxy-only host directly. Raises, so callers stop early."""
    if _is_proxy_only_host(url) and not scrape_proxy():
        raise ProxyRequiredError(
            f"Refusing to fetch {urlparse(url).netloc} directly: scraping it from this "
            f"address risks the operator's own account. Set {_PROXY_ENV_VAR} to an egress "
            "proxy to allow it, or find the employer's own posting URL instead — most "
            "employers' ATS (Greenhouse / Lever / Workday / BambooHR) serve the same JD."
        )


class ProxyRequiredError(RuntimeError):
    """Raised when a host may only be fetched through a configured proxy."""


def _curl_get(url: str, *, timeout: int = 30, proxy: str = "") -> str:
    """Fetch `url` with the system curl. Returns "" if curl is missing or fails."""
    import shutil
    import subprocess

    curl = shutil.which("curl")
    if not curl:
        return ""
    args = [curl, "-sSL", "--compressed", "--max-time", str(timeout)]
    if proxy:
        args += ["--proxy", proxy]
    try:
        done = subprocess.run(
            [*args, url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _client(*, proxy: str = "") -> httpx.AsyncClient:
    kwargs: dict[str, object] = {
        "follow_redirects": True,
        "timeout": 30,
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]


def _is_workday_url(url: str) -> bool:
    return "myworkdayjobs.com" in urlparse(url).netloc


def _workday_company_from_url(url: str) -> str:
    parsed = urlparse(url)
    if "myworkdayjobs.com" not in parsed.netloc:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        return re.sub(r"[-_]+", " ", parts[1]).strip()
    subdomain = parsed.netloc.split(".")[0]
    tenant = subdomain.split("_")[0]
    return re.sub(r"[-_]+", " ", tenant).strip().title()


def _workday_location_from_text(text: str) -> str:
    match = re.search(r"(?:^|\n)locations?\s*\n([^\n]+)", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _greenhouse_api_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc not in {"job-boards.greenhouse.io", "boards.greenhouse.io"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[1] == "jobs":
        return f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs/{parts[2]}"
    return ""


def _company_from_ats_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    slug = parts[0]
    known = {
        "faire": "Faire",
        "cohere": "Cohere",
        "anthropic": "Anthropic",
        "affirm": "Affirm",
        "instacart": "Instacart",
    }
    return known.get(slug, re.sub(r"[-_]+", " ", slug).title())


def _lever_api_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc != "jobs.lever.co":
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2:
        return f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}"
    return ""


def _ashby_board_api_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc != "jobs.ashbyhq.com":
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2:
        return f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}"
    return ""


def _bamboohr_slug(host: str) -> str:
    """The employer subdomain of a BambooHR board, or "" for any other host.

    `vendasta.bamboohr.com` -> `vendasta`. The bare apex and `www` are the
    product's own marketing site, not an employer board. Lives here rather than
    in the scanner because extraction is the lower-level module of the two.
    """
    host = (host or "").lower()
    if not host.endswith(".bamboohr.com"):
        return ""
    slug = host[: -len(".bamboohr.com")]
    return "" if slug in {"", "www"} else slug


def _bamboohr_detail_api_url(url: str) -> str:
    """`…/careers/829` -> `…/careers/829/detail`.

    The human page is a JavaScript shell — its <title> is the literal string
    "BambooHR" and its body carries no posting text — so without this the JD
    reaches the scorer empty and the job comes back 0.0/SKIP, indistinguishable
    from a genuinely bad posting. The detail feed is the same JSON the page
    fetches for itself.
    """
    parsed = urlparse(url)
    if not _bamboohr_slug(parsed.netloc):
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "careers" and parts[1].isdigit():
        return f"https://{parsed.netloc}/careers/{parts[1]}/detail"
    return ""


def _bamboohr_company(url: str) -> str:
    slug = _bamboohr_slug(urlparse(url).netloc)
    return re.sub(r"[-_]+", " ", slug).title() if slug else ""


def _find_ashby_job(raw: dict, url: str) -> dict | None:
    parts = [part for part in urlparse(url).path.rstrip("/").split("/") if part]
    target_id = parts[-2] if len(parts) >= 2 and parts[-1] == "application" else (parts[-1] if parts else "")
    for job in raw.get("jobs") or []:
        if job.get("id") == target_id:
            return job
        for key in ("jobUrl", "applyUrl"):
            if str(job.get(key) or "").rstrip("/") == url.rstrip("/"):
                return job
    return None


def extract_html_title(html: str) -> str:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return clean_web_text(title_match.group(1)) if title_match else ""


def extract_html_body(html: str) -> str:
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.IGNORECASE | re.DOTALL)
    body = body_match.group(1) if body_match else html
    removable = r"<(script|style|noscript|svg|footer|nav|header|form)[^>]*>.*?</\1>"
    body = re.sub(removable, " ", body, flags=re.IGNORECASE | re.DOTALL)
    return clean_web_text(body)


def clean_web_text(value: str) -> str:
    text = unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|li|h[1-6]|section|article)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
