"""Chinese-language community job boards in Canada — technical postings only.

Why this exists: Chinese-owned and Chinese-Canadian employers post in Chinese
on community classifieds that no English aggregator syndicates. Measured
2026-09-16 over 20 such channels: two are readable without a login or an
anti-bot challenge and carry real volume, and they are the two read here.

- **51.ca 加国无忧** (Toronto-based) — ``/jobs/job-posts?page=N``, 15 cards a
  page, ~66 pages, ~990 live posts. The card carries title, district, employer
  handle and badges but no date; each posting page carries a schema.org
  ``JobPosting`` block with ``datePosted``, ``validThrough`` and the employer.
- **Vansky 温哥华天空** (Greater Vancouver) — ``/info/ZPQZ01.html?page=N``, rows
  in schema.org microdata with ``dateModified``. Pinned commercial ads repeat on
  every page and are always "fresh"; only free ads say how far back a page is.

Fewer than 1% of posts on either board are technical, so each listing is
screened on its title with ``triage.cn_technical_title`` — the same definition
triage scores with — before any detail page is fetched. Measured yield on
2026-09-16: one real fit (a systems & network administrator) in ~2,100 posts.

A board that answers 200 with a page this module cannot read — a challenge
page, or changed markup — is counted as an error, never as a quiet day. This
tier does not work around challenges; it reports them. Deliberately not read at
all, because each needs a login or answers with an anti-bot challenge: 约克论坛
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

from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult, from_row
from job_hunt.services.triage import cn_technical_title

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

FIFTYONE_BASE = "https://www.51.ca"
FIFTYONE_LIST = f"{FIFTYONE_BASE}/jobs/job-posts"
VANSKY_BASE = "https://www.vansky.com/info/"
VANSKY_LIST = f"{VANSKY_BASE}ZPQZ01.html"

# 51.ca writes the locality in Chinese. Only names that are unambiguous are
# mapped; anything else is kept as written with ", Canada" and reads as an
# unknown region — 51.ca is Toronto-based, but a posting there can be in Ottawa,
# and the first version filed every one of them as Greater Toronto.
_51CA_LOCALITIES = {
    "多市中心": "Toronto, ON", "多伦多": "Toronto, ON", "北约克": "North York, ON",
    "士嘉堡": "Scarborough, ON", "怡陶碧谷": "Etobicoke, ON", "万锦": "Markham, ON",
    "列治文山": "Richmond Hill, ON", "密西沙加": "Mississauga, ON", "旺市": "Vaughan, ON",
    "宾顿": "Brampton, ON", "奥克维尔": "Oakville, ON", "奥罗拉": "Aurora, ON",
    "纽马克特": "Newmarket, ON", "皮克林": "Pickering, ON", "阿贾克斯": "Ajax, ON",
    "惠特比": "Whitby, ON", "奥沙瓦": "Oshawa, ON", "伯灵顿": "Burlington, ON",
    "大多地区": "Greater Toronto Area, ON", "汉密尔顿": "Hamilton, ON",
    "滑铁卢": "Waterloo, ON", "基奇纳": "Kitchener, ON", "圭尔夫": "Guelph, ON",
    "渥太华": "Ottawa, ON", "京士顿": "Kingston, ON", "巴里": "Barrie, ON",
    "伦敦": "London, ON", "温莎": "Windsor, ON", "尼亚加拉": "Niagara, ON",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def _cell(value: str) -> str:
    """The pipeline inbox is a ` | `-separated line; a pipe inside a field splits it."""
    return value.replace("|", "/").strip()


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
        address = (data.get("jobLocation") or {}).get("address") or {}
        return {
            "title": _text(data.get("title", "")),
            "company": _text((data.get("hiringOrganization") or {}).get("name", "")),
            "posted": str(data.get("datePosted", ""))[:10],
            "closes": str(data.get("validThrough", ""))[:10],
            "locality": _text(address.get("addressLocality", "")),
        }
    return {}


def _51ca_location(locality: str) -> str:
    if not locality:
        return "Canada"
    mapped = _51CA_LOCALITIES.get(locality)
    return f"{mapped}, Canada" if mapped else f"{locality}, Canada"


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
        city = re.search(r'<td class="adph-font">\s*<div>\s*([^<]*?)\s*</div>', block)
        rows.append(
            {
                "url": VANSKY_BASE + path,
                "title": _text(title),
                "company": _text(meta("author")),
                "posted": _vansky_date(meta("dateModified")),
                "city": _text(city.group(1)) if city else "",
                # Pinned commercial ads sit on every page with a fresh date; only
                # free ads tell the walk how far back the listing has gone.
                "pinned": "" if path.startswith("adfree/") else "1",
            }
        )
    return rows


# --------------------------------------------------------------------------- sweep


def _get(client: httpx.Client, url: str, retries: int, sleep: Callable[[float], None]) -> str | None:
    """Body of a 200, or None. Vansky answers an occasional 503 that a retry clears."""
    for attempt in range(retries + 1):
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response.text
        except httpx.HTTPError:
            pass
        if attempt < retries:
            sleep(5.0)
    return None


def _scan_51ca(client, cfg, *, delay, sleep, today) -> tuple[list[dict[str, str]], dict[str, Any]]:
    max_pages = int(cfg.get("max_pages", 90))
    seen: set[str] = set()
    matches: list[dict[str, str]] = []
    health = {"errors": 0, "truncated": False, "note": ""}
    for page in range(1, max_pages + 1):
        if page > 1 and delay > 0:
            sleep(delay)
        body = _get(client, f"{FIFTYONE_LIST}?page={page}", 1, sleep)
        if body is None:
            health["errors"] += 1
            break
        cards = parse_51ca_list(body)
        if not cards:
            # 51.ca answers a page past the end with the last page again, never
            # an empty one, so no cards at all is a 200 this module cannot read:
            # a challenge page or changed markup (checked 2026-09-16, pages 66-120).
            health["errors"] += 1
            health["note"] = f"51ca page {page} unreadable"
            break
        fresh = [card for card in cards if card["id"] not in seen]
        if not fresh:
            break
        seen.update(card["id"] for card in fresh)
        matches.extend(card for card in fresh if cn_technical_title(card["title"]))
        if page == max_pages:
            health["truncated"] = True

    rows: list[dict[str, str]] = []
    for card in matches:
        if delay > 0:
            sleep(delay)
        body = _get(client, card["url"], 1, sleep)
        detail = parse_51ca_detail(body) if body is not None else {}
        if not detail:
            health["errors"] += 1
        if detail.get("closes") and detail["closes"] < today.strftime("%Y-%m-%d"):
            continue
        rows.append(
            {
                "url": card["url"],
                "title": _cell(detail.get("title") or card["title"]),
                "company": _cell(detail.get("company") or card["company"]),
                "location": _51ca_location(detail.get("locality") or card["district"]),
                "posted": detail.get("posted", ""),
                "closes": detail.get("closes", ""),
                "board": "51ca",
            }
        )
    return rows, health


def _scan_vansky(client, cfg, *, delay, sleep, today) -> tuple[list[dict[str, str]], dict[str, Any]]:
    max_pages = int(cfg.get("max_pages", 40))
    cutoff = (today - timedelta(days=int(cfg.get("max_age_days", 14)))).strftime("%Y-%m-%d")
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    health = {"errors": 0, "truncated": False, "note": ""}
    for page in range(1, max_pages + 1):
        if page > 1 and delay > 0:
            sleep(delay)
        body = _get(client, f"{VANSKY_LIST}?page={page}", 3, sleep)
        if body is None:
            health["errors"] += 1
            break
        listing = parse_vansky_list(body)
        if not listing:
            # Vansky has no empty last page: the walk ends on the date cutoff,
            # so a page with no rows at all is a page this module cannot read.
            health["errors"] += 1
            health["note"] = f"vansky page {page} unreadable"
            break
        fresh = [row for row in listing if row["url"] not in seen]
        if not fresh:
            break
        for row in fresh:
            seen.add(row["url"])
            if row["posted"] and row["posted"] < cutoff:
                continue
            if not cn_technical_title(row["title"]):
                continue
            city = row["city"]
            rows.append(
                {
                    "url": row["url"],
                    "title": _cell(row["title"]),
                    "company": _cell(row["company"]),
                    "location": f"{city}, BC, Canada" if city else "Greater Vancouver, BC, Canada",
                    "posted": row["posted"],
                    "closes": "",
                    "board": "vansky",
                }
            )
        free_dates = [row["posted"] for row in listing if not row["pinned"] and row["posted"]]
        if free_dates and max(free_dates) < cutoff:
            break
        if page == max_pages:
            health["truncated"] = True
    return rows, health


def scan_cn_boards_source(
    config: dict[str, Any] | None,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    today: datetime | None = None,
) -> SourceResult:
    """Technical postings from the enabled boards, with one health for the tier."""
    boards = (config or {}).get("boards") or {}
    if not config or not config.get("enabled", False):
        return SourceResult(postings=[], health=SourceHealth(source_id="cn_boards", ok=True))
    delay = float(config.get("delay_s", 0.6))
    timeout = float(config.get("timeout_s", 30.0))
    today = today or datetime.now()
    owns_client = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=timeout, follow_redirects=True)
    rows: list[dict[str, str]] = []
    errors = 0
    truncated = False
    notes: list[str] = []
    try:
        for board_id, sweep in (("51ca", _scan_51ca), ("vansky", _scan_vansky)):
            cfg = boards.get(board_id) or {}
            if not cfg.get("enabled", False):
                continue
            board_rows, health = sweep(client, cfg, delay=delay, sleep=sleep, today=today)
            rows.extend(board_rows)
            errors += health["errors"]
            truncated = truncated or health["truncated"]
            if health["note"]:
                notes.append(health["note"])
    finally:
        if owns_client:
            client.close()

    postings: list[JobPosting] = []
    for row in rows:
        posting = from_row(
            {**row, "company": row["company"] or "Unknown (see posting)", "source": row["board"]},
            source_id=f"cn:{row['board']}",
            portal=row["board"],
        )
        if posting is not None:
            postings.append(posting)
    return SourceResult(
        postings=postings,
        health=SourceHealth(
            source_id="cn_boards",
            ok=errors == 0,
            collected=len(postings),
            truncated=truncated,
            errors=errors,
            note="; ".join(notes),
        ),
    )
