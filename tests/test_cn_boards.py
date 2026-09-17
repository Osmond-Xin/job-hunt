"""Chinese-language community boards — 51.ca and Vansky."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from job_hunt.services.cn_boards import scan_cn_boards_source
from job_hunt.services.triage import cn_technical_title

TODAY = datetime(2026, 9, 16)


def _51_card(post_id: str, title: str, district: str = "北约克", employer: str = "someone") -> str:
    return f"""
<div class="job-item highlight" data-role="job-item-1" data-id="{post_id}">
    <a href="https://www.51.ca/jobs/job-posts/{post_id}?from=searchlist">
        <div class="item-title fc-black fw-medium font-ellipsis">{title}</div>
        <div class="rate hstack xxs y-center">
            <div class="fs-xs fc-gray work-place-address">
                <div class="font-ellipsis"> · {district}</div>
            </div>
        </div>
    </a>
    <div class="employer d-md-block fl-base">
        <div class="font-ellipsis item-employer-name"><span class="fc-light-black">{employer}</span></div>
    </div>
</div>
"""


def _51_detail(title: str, company: str, locality: str = "列治文山", closes: str = "2026-09-25") -> str:
    return (
        '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",'
        f'"title":"{title}","datePosted":"2026-09-14T09:01:45-04:00",'
        f'"validThrough":"{closes}T23:59:59-04:00","hiringOrganization":{{"@type":"Organization",'
        f'"name":"{company}"}},"jobLocation":{{"@type":"Place","address":{{"@type":"PostalAddress",'
        f'"addressLocality":"{locality}"}}}}}}</script>'
    )


def _vansky_row(path: str, title: str, modified: str, author: str = "HR") -> str:
    return f"""
<tr itemprop="itemListElement" itemscope="" itemtype="http://schema.org/Article">
<td width="40">
<meta itemprop="author" content="{author}">
<meta itemprop="dateModified" content="{modified} GMT-0700 (Pacific Daylight Time)">
<meta itemprop="headline" content="{title}">
<meta itemprop="mainEntityOfPage" content="{path}"/>
</td>
<td class="adph-font"><div>Burnaby</div></td>
</tr>
"""


def _client(routes: dict[str, str], hits: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        if hits is not None:
            hits.append(key)
        if key in routes:
            return httpx.Response(200, text=routes[key])
        return httpx.Response(404, text="")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _scan(boards: dict, routes: dict[str, str], hits: list[str] | None = None):
    return scan_cn_boards_source(
        {"enabled": True, "delay_s": 0, "boards": boards},
        client=_client(routes, hits),
        sleep=lambda _: None,
        today=TODAY,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Systems & Network Administrator",
        "大型电脑公司招聘电脑测试员,无需工作经验",
        "IT support 技术支持",
        "Python 后端开发",
        "网管",
        "SDE 软件开发招聘",
        # Latin and CJK with no space between (Codex review 2026-09-16).
        "招聘IT工程师",
        "AI工程师",
        "招聘Python开发",
        # 仓库 / 会计 describe the system being built here (Codex review round 2).
        "数据仓库开发工程师",
        "会计软件开发工程师",
        "前端开发工程师",
        "数据库管理员",
    ],
)
def test_technical_titles_pass_the_screen(title):
    assert cn_technical_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "卡车司机",
        "暖通安装技术员 HVAC",
        "餐馆企台",
        "AIR conditioning",
        # Incidental computer words in a non-technical job (Codex review 2026-09-16).
        "电脑公司招聘仓库搬运工",
        "熟悉电脑的餐厅收银员",
        "5星诊所聘请前台兼职+AI marketing project",
        "Rootmaths.com新未来学院诚聘数学、英语、计算机编程等网课老师",
        # A warehouse administrator who uses ERP (Codex review round 3).
        "仓库管理员 熟悉ERP",
    ],
)
def test_non_technical_titles_do_not(title):
    assert not cn_technical_title(title)


def test_51ca_fetches_detail_pages_only_for_technical_titles():
    base = "https://www.51.ca/jobs/job-posts"
    routes = {
        f"{base}?page=1": _51_card("1", "餐馆企台") + _51_card("2", "电脑测试员"),
        # Past the end, 51.ca serves the last page again.
        f"{base}?page=2": _51_card("1", "餐馆企台") + _51_card("2", "电脑测试员"),
        f"{base}/2": _51_detail("电脑测试员 | 入门", "ALC Micro"),
    }
    hits: list[str] = []
    result = _scan({"51ca": {"enabled": True}}, routes, hits)
    assert f"{base}/1" not in hits
    [posting] = result.postings
    assert posting.company == "ALC Micro"
    assert posting.title == "电脑测试员 / 入门"  # a pipe would split the inbox line
    assert posting.location == "Richmond Hill, ON, Canada"
    assert posting.posted == "2026-09-14"
    assert posting.portal == "51ca"
    assert result.health.ok and result.health.collected == 1


def test_51ca_does_not_file_an_unmapped_locality_as_toronto():
    base = "https://www.51.ca/jobs/job-posts"
    routes = {
        f"{base}?page=1": _51_card("7", "网管"),
        f"{base}?page=2": _51_card("7", "网管"),
        f"{base}/7": _51_detail("网管", "Co", locality="某小镇"),
    }
    [posting] = _scan({"51ca": {"enabled": True}}, routes).postings
    assert posting.location == "某小镇, Canada"


def test_51ca_drops_a_posting_past_its_closing_date():
    base = "https://www.51.ca/jobs/job-posts"
    routes = {
        f"{base}?page=1": _51_card("8", "网管"),
        f"{base}?page=2": _51_card("8", "网管"),
        f"{base}/8": _51_detail("网管", "Co", closes="2026-09-01"),
    }
    assert _scan({"51ca": {"enabled": True}}, routes).postings == []


def test_51ca_page_budget_is_reported_as_truncated():
    base = "https://www.51.ca/jobs/job-posts"
    routes = {f"{base}?page=1": _51_card("1", "餐馆企台"), f"{base}?page=2": _51_card("2", "餐馆企台")}
    result = _scan({"51ca": {"enabled": True, "max_pages": 2}}, routes)
    assert result.health.truncated


def test_a_challenge_page_is_an_error_not_a_quiet_day():
    """A 200 with no readable listing used to return zero rows and errors=0."""
    challenge = "<html><title>Verify you are human</title></html>"
    routes = {
        "https://www.51.ca/jobs/job-posts?page=1": challenge,
        "https://www.vansky.com/info/ZPQZ01.html?page=1": challenge,
    }
    result = _scan({"51ca": {"enabled": True}, "vansky": {"enabled": True}}, routes)
    assert result.postings == []
    assert result.health.errors == 2
    assert not result.health.ok
    assert "unreadable" in result.health.note


def test_a_failed_request_is_counted_not_silent():
    result = _scan({"51ca": {"enabled": True}}, {})
    assert result.health.errors == 1


def test_vansky_walk_stops_once_free_ads_are_older_than_the_window():
    base = "https://www.vansky.com/info/ZPQZ01.html"
    pinned = _vansky_row("adcommercial/1.html", "IT 招聘", "Wed Sep 16 2026 09:00:00")
    routes = {
        f"{base}?page=1": pinned
        + _vansky_row("adfree/10.html", "Systems & Network Administrator", "Mon Sep 14 2026 10:00:00")
        + _vansky_row("adfree/13.html", "丽晶广场诚招前台服务员", "Mon Sep 14 2026 11:00:00"),
        f"{base}?page=2": pinned
        + _vansky_row("adfree/11.html", "Python developer", "Mon Aug 10 2026 10:00:00"),
        f"{base}?page=3": _vansky_row("adfree/12.html", "网管", "Tue Sep 15 2026 10:00:00"),
    }
    hits: list[str] = []
    result = _scan({"vansky": {"enabled": True, "max_age_days": 14}}, routes, hits)
    assert f"{base}?page=3" not in hits
    assert [p.url for p in result.postings] == [
        "https://www.vansky.com/info/adcommercial/1.html",
        "https://www.vansky.com/info/adfree/10.html",
    ]
    assert result.postings[1].location == "Burnaby, BC, Canada"


def test_disabled_config_does_nothing():
    assert scan_cn_boards_source({"enabled": False}).postings == []
    assert scan_cn_boards_source(None).postings == []


def test_two_chinese_titles_from_one_poster_are_not_merged_as_duplicates():
    # normalize() keeps ASCII only, so both titles used to share the key
    # ("robert", "") and the second posting was dropped. Tested at
    # `_accept_jobs` because `scan_portals` builds its own clients and reads
    # the tracker from disk, so there is no seam above it to drive two rows through.
    from job_hunt.services.scan import ScanResult, ScannedJob, _accept_jobs

    jobs = [
        ScannedJob(url=f"https://www.vansky.com/info/adfree/{n}.html", title=title, company="Robert",
                   location="Coquitlam, BC, Canada", portal="vansky", source="vansky")
        for n, title in [(1, "计算机编程开发"), (2, "网管")]
    ]
    result = ScanResult()
    _accept_jobs(jobs, result, positives=[], negatives=[], include_non_canada=False,
                 known_urls=set(), known_company_roles=set(), apply=False, require_positive=False)
    assert result.new_jobs == 2
    assert result.skipped_duplicates == 0
