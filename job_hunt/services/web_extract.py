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

    return None


async def _http_extract(url: str) -> WebExtractResult:
    async with _client() as client:
        response = await client.get(url)
        response.raise_for_status()
    html = response.text
    title = extract_html_title(html)
    body = extract_html_body(html)
    return WebExtractResult(
        url=str(response.url),
        text=clean_web_text("\n\n".join(part for part in [title, body] if part)),
        adapter="http_extract",
        title=title,
    )


async def _try_playwright_extract(url: str) -> WebExtractResult | None:
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
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


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
    )


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
