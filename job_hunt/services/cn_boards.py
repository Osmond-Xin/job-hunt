"""Chinese-language community job boards in Canada — technical postings only.

Why this exists: Chinese-owned and Chinese-Canadian employers post in Chinese
on community classifieds that no English aggregator syndicates. Measured
2026-09-16 over 20 such channels: two are readable without a login or an
anti-bot challenge and carry real volume, and they are the two read here.

- **51.ca 加国无忧** (Greater Toronto) — ``/jobs/job-posts?page=N``, 15 cards a
  page, ~66 pages, ~990 live posts. The card carries title, district, employer
  handle and badges but no date; each posting page carries a schema.org
  ``JobPosting`` block with ``datePosted``, ``validThrough`` and the employer.
- **Vansky 温哥华天空** (Greater Vancouver) — ``/info/ZPQZ01.html?page=N``, rows
  in schema.org microdata with ``dateModified``. Pinned commercial ads repeat on
  every page and are always "fresh"; only free ads say how far back a page is.

Fewer than 1% of posts on either board are technical — the rest are
restaurant, trades, retail and office work — so the listing is screened on a
tech keyword list *before* any detail page is fetched. A posting that slips
through is triaged like any other; one that is screened out never costs a
request. Measured yield on 2026-09-16: one real fit (a systems & network
administrator) in ~2,100 posts.

Deliberately not read, because every one of them needs a login or answers with
an anti-bot challenge and this tier does not work around those: 约克论坛
(signed API), 温哥华家园 (Cloudflare), rolia, CFC, BOSS直聘, 猎聘, 51job, 智联,
小红书.
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

FIFTYONE_BASE = "https://www.51.ca"
FIFTYONE_LIST = f"{FIFTYONE_BASE}/jobs/job-posts"
VANSKY_BASE = "https://www.vansky.com/info/"
VANSKY_LIST = f"{VANSKY_BASE}ZPQZ01.html"

# Title-level screen. Kept to words that name the work, in simplified and
# traditional Chinese and English. Broad words like "system", "support",
# "engineer" or "technician" are left out on purpose: on these boards they are
# mostly HVAC, renovation and security-camera installers.
TECH_RE = re.compile(
    r"(\bIT\b|I\.T\.|电脑|電腦|计算机|計算機|软件|軟件|程序员|程序員|编程|編程|"
    r"开发工程师|開發工程師|前端|后端|後端|全栈|全棧|数据分析|數據分析|数据库|數據庫|"
    r"数据工程|數據工程|网管|網管|网络管理|網絡管理|系统管理|系統管理|运维|運維|"
    r"技术支持|技術支持|人工智能|网站开发|網站開發|"
    r"\bdevelopers?\b|\bsoftware\b|\bprogrammers?\b|\bdata (?:analyst|engineer|scientist)s?\b|"
    r"\bdatabase\b|\bnetwork (?:admin|administrator|engineer|technician)s?\b|"
    r"\bsys ?admin|\bsystems? administrator|\bhelp ?desk\b|\bdevops\b|\bcloud engineer|"
    r"\bAI\b|\bLLM\b|\bpython\b|\bjava\b|\bweb developer|\bERP\b|\bpower ?bi\b)",
    re.I,
)

_TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def _cell(value: str) -> str:
    """The pipeline inbox is a ` | `-separated line; a pipe inside a field splits it."""
    return value.replace("|", "/").strip()


def is_technical(text: str) -> bool:
    return bool(TECH_RE.search(text or ""))


# --------------------------------------------------------------------------- 51.ca

_51_CARD_RE = re.compile(r'<div class="job-item[ "].*?(?=<div class="job-item[ "]|\Z)', re.S)


def parse_51ca_list(page_html: str) -> list[dict[str, str]]:
    """Cards from one 51.ca listing page. No date here — that is on the posting."""
    rows: list[dict[str, str]] = []
    for card in _51_CARD_RE.findall(page_html or ""):
        post_id = re.search(r'data-id="(\d+)"', card)
        title = re.search(r'class="item-title[^"]*">(.*?)</div>', card, re.S)
        if not post_id or not title:
            continue
        district = re.search(r'work-place-address">\s*<div[^>]*>\s*·?\s*(.*?)</div>', card, re.S)
        employer = re.search(r'item-employer-name">\s*<span[^>]*>(.*?)</span>', card, re.S)
        rows.append(
            {
                "id": post_id.group(1),
                "url": f"{FIFTYONE_LIST}/{post_id.group(1)}",
                "title": _text(title.group(1)),
                "district": _text(district.group(1)).lstrip("· ").strip() if district else "",
                "company": _text(employer.group(1)) if employer else "",
                "badges": " ".join(_text(b) for b in re.findall(r'item-badge[^>]*>(.*?)</span>', card, re.S)),
            }
        )
    return rows


def parse_51ca_detail(page_html: str) -> dict[str, str]:
    """Date, closing date, employer and locality from the posting's JobPosting block."""
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page_html or "", re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "JobPosting":
            continue
        address = ((data.get("jobLocation") or {}).get("address") or {})
        return {
            "title": _text(data.get("title", "")),
            "company": _text((data.get("hiringOrganization") or {}).get("name", "")),
            "posted": str(data.get("datePosted", ""))[:10],
            "closes": str(data.get("validThrough", ""))[:10],
            "locality": _text(address.get("addressLocality", "")),
            "description": _text(data.get("description", "")),
        }
    return {}


# --------------------------------------------------------------------------- Vansky

_VANSKY_ROW_RE = re.compile(r'itemprop="itemListElement"(.*?)(?=itemprop="itemListElement"|\Z)', re.S)


def _vansky_date(value: str) -> str:
    """``Tue Sep 15 2026 15:24:39 GMT-0700 (...)`` → ``2026-09-15``."""
    try:
        return datetime.strptime(value[:24], "%a %b %d %Y %H:%M:%S").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_vansky_list(page_html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for block in _VANSKY_ROW_RE.findall(page_html or ""):
        def meta(key: str) -> str:
            match = re.search(rf'itemprop="{key}" content="([^"]*)"', block)
            return html.unescape(match.group(1)) if match else ""

        path = meta("mainEntityOfPage")
        title = meta("headline")
        if not path or not title:
            continue
        description = re.search(r'itemprop="description"[^>]*>(.*?)</div>', block, re.S)
        city = re.search(r'<td class="adph-font">\s*<div>\s*([^<]*?)\s*</div>', block)
        rows.append(
            {
                "url": VANSKY_BASE + path,
                "title": _text(title),
                "company": _text(meta("author")),
                "posted": _vansky_date(meta("dateModified")),
                "description": _text(description.group(1)) if description else "",
                "city": _text(city.group(1)) if city else "",
                # Pinned commercial ads sit on every page with a fresh date; only
                # free ads tell the walk how far back the listing has gone.
                "pinned": "" if path.startswith("adfree/") else "1",
            }
        )
    return rows


# --------------------------------------------------------------------------- sweep


def _get(client: httpx.Client, url: str, retries: int, sleep: Callable[[float], None]) -> tuple[str, bool]:
    """Vansky answers an occasional 503 that a retry a few seconds later clears."""
    for attempt in range(retries + 1):
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response.text, False
        except httpx.HTTPError:
            pass
        if attempt < retries:
            sleep(5.0)
    return "", True


def _scan_51ca(client, cfg, *, delay, sleep, stats) -> list[dict[str, str]]:
    max_pages = int(cfg.get("max_pages", 90))
    seen: set[str] = set()
    matches: list[dict[str, str]] = []
    errors = 0
    truncated = False
    read = 0
    for page in range(1, max_pages + 1):
        if page > 1 and delay > 0:
            sleep(delay)
        body, failed = _get(client, f"{FIFTYONE_LIST}?page={page}", 1, sleep)
        if failed:
            errors += 1
            break
        fresh = [row for row in parse_51ca_list(body) if row["id"] not in seen]
        if not fresh:
            break
        for row in fresh:
            seen.add(row["id"])
        read += len(fresh)
        matches.extend(row for row in fresh if is_technical(row["title"]))
        if page == max_pages:
            truncated = True

    out: list[dict[str, str]] = []
    for row in matches:
        if delay > 0:
            sleep(delay)
        body, failed = _get(client, row["url"], 1, sleep)
        detail = {} if failed else parse_51ca_detail(body)
        errors += int(failed)
        locality = detail.get("locality") or row["district"]
        out.append(
            {
                "board": "51ca",
                "url": row["url"],
                "title": _cell(detail.get("title") or row["title"]),
                "company": _cell(detail.get("company") or row["company"]),
                "location": f"{locality}, Greater Toronto Area, ON, Canada" if locality else "Greater Toronto Area, ON, Canada",
                "posted": detail.get("posted", ""),
                "closes": detail.get("closes", ""),
            }
        )
    stats["51ca"] = {"collected": len(out), "read": read, "errors": errors, "truncated": truncated}
    return out


def _scan_vansky(client, cfg, *, delay, sleep, stats, today) -> list[dict[str, str]]:
    max_pages = int(cfg.get("max_pages", 40))
    cutoff = (today - timedelta(days=int(cfg.get("max_age_days", 14)))).strftime("%Y-%m-%d")
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    errors = 0
    truncated = False
    read = 0
    for page in range(1, max_pages + 1):
        if page > 1 and delay > 0:
            sleep(delay)
        body, failed = _get(client, f"{VANSKY_LIST}?page={page}", 3, sleep)
        if failed:
            errors += 1
            break
        rows = parse_vansky_list(body)
        fresh = [row for row in rows if row["url"] not in seen]
        if not fresh:
            break
        for row in fresh:
            seen.add(row["url"])
            if row["posted"] and row["posted"] < cutoff:
                continue
            read += 1
            # Title only. Descriptions ask for "熟练掌握电脑" in front-desk and
            # restaurant ads, which matched two of them on the first live run.
            if not is_technical(row["title"]):
                continue
            city = row["city"]
            out.append(
                {
                    "board": "vansky",
                    "url": row["url"],
                    "title": _cell(row["title"]),
                    "company": _cell(row["company"]),
                    "location": f"{city}, BC, Canada" if city else "Greater Vancouver, BC, Canada",
                    "posted": row["posted"],
                    "closes": "",
                }
            )
        free_dates = [row["posted"] for row in rows if not row["pinned"] and row["posted"]]
        if free_dates and max(free_dates) < cutoff:
            break
        if page == max_pages:
            truncated = True
    stats["vansky"] = {"collected": len(out), "read": read, "errors": errors, "truncated": truncated}
    return out


def scan_cn_boards(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stats: dict[str, dict[str, Any]] | None = None,
    today: datetime | None = None,
) -> list[dict[str, str]]:
    """Technical postings from the enabled boards. Same contract as ``scan_regional_boards``."""
    if not config or not config.get("enabled", False):
        return []
    boards = config.get("boards") or {}
    delay = float(config.get("delay_s", 0.6))
    timeout = float(config.get("timeout_s", 30.0))
    stats = stats if stats is not None else {}
    owns_client = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=timeout, follow_redirects=True)
    try:
        out: list[dict[str, str]] = []
        cfg = boards.get("51ca") or {}
        if cfg.get("enabled", False):
            out.extend(_scan_51ca(client, cfg, delay=delay, sleep=sleep, stats=stats))
        cfg = boards.get("vansky") or {}
        if cfg.get("enabled", False):
            out.extend(
                _scan_vansky(client, cfg, delay=delay, sleep=sleep, stats=stats, today=today or datetime.now())
            )
        return out
    finally:
        if owns_client:
            client.close()
