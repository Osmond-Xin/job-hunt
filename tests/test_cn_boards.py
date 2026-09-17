"""Chinese-language community boards — 51.ca and Vansky."""

from __future__ import annotations

from datetime import datetime

import httpx

from job_hunt.services.cn_boards import (
    is_technical,
    parse_51ca_detail,
    parse_51ca_list,
    parse_vansky_list,
    scan_cn_boards,
)


def _51_card(post_id: str, title: str, district: str = "北约克", employer: str = "someone") -> str:
    return f"""
<div class="job-item highlight" data-role="job-item-1" data-id="{post_id}">
    <a href="https://www.51.ca/jobs/job-posts/{post_id}?from=searchlist">
        <div class="item-title fc-black fw-medium font-ellipsis">{title}</div>
        <div class="rate hstack xxs y-center">
            <span class="fs-s fc-gray">薪资面议</span>
            <div class="fs-xs fc-gray work-place-address">
                <div class="font-ellipsis"> · {district}</div>
            </div>
        </div>
        <div class="badge-box">
            <span class="item-badge item-badge-default fs-xxs fc-gray font br-s">全职</span>
            <span class="item-badge item-badge-default fs-xxs fc-gray font br-s">需要工作签证</span>
        </div>
    </a>
    <div class="employer d-md-block fl-base">
        <div class="font-ellipsis item-employer-name"><span class="fc-light-black">{employer}</span></div>
    </div>
</div>
"""


def _51_detail(title: str, company: str) -> str:
    return (
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",'
        f'"title":"{title}","description":"x","datePosted":"2026-09-14T09:01:45-04:00",'
        '"validThrough":"2026-09-25T23:59:59-04:00","hiringOrganization":{"@type":"Organization",'
        f'"name":"{company}"}},"jobLocation":{{"@type":"Place","address":{{"@type":"PostalAddress",'
        '"addressLocality":"列治文山"}}}</script>'
        '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
    )


def _vansky_row(path: str, title: str, modified: str, author: str = "HR", description: str = "") -> str:
    return f"""
<tr itemprop="itemListElement" itemscope="" itemtype="http://schema.org/Article">
<td width="40">
<meta itemprop="author" content="{author}">
<meta itemprop="datePublished" content="Fri Sep 24 2021 14:53:31 GMT-0700 (Pacific Daylight Time)">
<meta itemprop="dateModified" content="{modified} GMT-0700 (Pacific Daylight Time)">
<meta itemprop="headline" content="{title}">
<meta itemprop="mainEntityOfPage" content="{path}"/>
</td>
<td><div itemprop="description" class="adsContentFont">{description}</div></td>
<td class="adph-font"><div>Burnaby</div></td>
</tr>
"""


def test_51ca_card_yields_title_district_and_employer_but_no_date():
    rows = parse_51ca_list(_51_card("1204029", "卡车司机（G牌/A牌）, 办公室OP", "怡陶碧谷", "jiajie2023"))
    assert rows == [
        {
            "id": "1204029",
            "url": "https://www.51.ca/jobs/job-posts/1204029",
            "title": "卡车司机（G牌/A牌）, 办公室OP",
            "district": "怡陶碧谷",
            "company": "jiajie2023",
            "badges": "全职 需要工作签证",
        }
    ]


def test_51ca_detail_reads_the_jobposting_block_not_the_breadcrumb():
    detail = parse_51ca_detail(_51_detail("电脑测试员", "ALC Micro"))
    assert detail["company"] == "ALC Micro"
    assert detail["posted"] == "2026-09-14"
    assert detail["closes"] == "2026-09-25"
    assert detail["locality"] == "列治文山"


def test_vansky_marks_pinned_commercial_ads_and_parses_dates():
    page = _vansky_row("adcommercial/14907.html", "大統華招聘", "Tue Sep 15 2026 15:24:39") + _vansky_row(
        "adfree/2847309.html", "Systems & Network Administrator", "Mon Sep 14 2026 10:00:00", "元初食品HR"
    )
    rows = parse_vansky_list(page)
    assert [row["pinned"] for row in rows] == ["1", ""]
    assert rows[1]["url"] == "https://www.vansky.com/info/adfree/2847309.html"
    assert rows[1]["posted"] == "2026-09-14"
    assert rows[1]["city"] == "Burnaby"


def test_the_screen_keeps_tech_work_and_drops_trades():
    for title in ["Systems & Network Administrator", "电脑测试员", "IT support 技术支持", "Python 后端开发", "网管"]:
        assert is_technical(title), title
    # Installers and HVAC are the bulk of "system"/"engineer" hits on these boards.
    for title in ["卡车司机", "暖通安装技术员 HVAC", "监控安装 system installer", "餐馆企台", "AIR conditioning"]:
        assert not is_technical(title), title


def _client(routes: dict[str, str], hits: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        hits.append(key)
        if key in routes:
            return httpx.Response(200, text=routes[key])
        return httpx.Response(404, text="")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_51ca_fetches_detail_pages_only_for_technical_titles():
    base = "https://www.51.ca/jobs/job-posts"
    routes = {
        f"{base}?page=1": _51_card("1", "餐馆企台") + _51_card("2", "电脑测试员"),
        # WordPress-style overshoot: the next page repeats what we have.
        f"{base}?page=2": _51_card("1", "餐馆企台"),
        f"{base}/2": _51_detail("电脑测试员 | 入门", "ALC Micro"),
    }
    hits: list[str] = []
    stats: dict = {}
    rows = scan_cn_boards(
        {"enabled": True, "delay_s": 0, "boards": {"51ca": {"enabled": True}}},
        client=_client(routes, hits),
        sleep=lambda _: None,
        stats=stats,
    )
    assert f"{base}/1" not in hits
    assert len(rows) == 1
    row = rows[0]
    assert row["company"] == "ALC Micro"
    assert row["title"] == "电脑测试员 / 入门"  # a pipe would split the inbox line
    assert row["location"] == "列治文山, Greater Toronto Area, ON, Canada"
    assert row["posted"] == "2026-09-14"
    assert stats["51ca"] == {"collected": 1, "read": 2, "errors": 0, "truncated": False}


def test_vansky_walk_stops_once_free_ads_are_older_than_the_window():
    base = "https://www.vansky.com/info/ZPQZ01.html"
    pinned = _vansky_row("adcommercial/1.html", "IT 招聘 pinned", "Wed Sep 16 2026 09:00:00")
    routes = {
        f"{base}?page=1": pinned
        + _vansky_row("adfree/10.html", "Systems & Network Administrator", "Mon Sep 14 2026 10:00:00")
        + _vansky_row("adfree/13.html", "丽晶广场诚招前台服务员", "Mon Sep 14 2026 11:00:00", description="熟悉电脑点餐系统"),
        f"{base}?page=2": pinned
        + _vansky_row("adfree/11.html", "Python developer", "Mon Aug 10 2026 10:00:00"),
        f"{base}?page=3": _vansky_row("adfree/12.html", "网管", "Tue Sep 15 2026 10:00:00"),
    }
    hits: list[str] = []
    rows = scan_cn_boards(
        {"enabled": True, "delay_s": 0, "boards": {"vansky": {"enabled": True, "max_age_days": 14}}},
        client=_client(routes, hits),
        sleep=lambda _: None,
        today=datetime(2026, 9, 16),
    )
    assert f"{base}?page=3" not in hits
    assert [row["url"] for row in rows] == [
        "https://www.vansky.com/info/adcommercial/1.html",
        "https://www.vansky.com/info/adfree/10.html",
    ]
    assert rows[1]["location"] == "Burnaby, BC, Canada"


def test_a_failed_listing_page_is_counted_not_silent():
    stats: dict = {}
    rows = scan_cn_boards(
        {"enabled": True, "delay_s": 0, "boards": {"51ca": {"enabled": True}}},
        client=_client({}, []),
        sleep=lambda _: None,
        stats=stats,
    )
    assert rows == []
    assert stats["51ca"]["errors"] == 1


def test_disabled_config_does_nothing():
    assert scan_cn_boards({"enabled": False}) == []
    assert scan_cn_boards(None) == []


def test_two_chinese_titles_from_one_poster_are_not_merged_as_duplicates():
    # normalize() keeps ASCII only, so both titles used to share the key
    # ("robert", "") and the second posting was dropped.
    from job_hunt.services.scan import ScanResult, ScannedJob, _accept_jobs

    jobs = [
        ScannedJob(url=f"https://www.vansky.com/info/adfree/{n}.html", title=title, company="Robert",
                   location="Coquitlam, BC, Canada", portal="vansky", source="vansky")
        for n, title in [(1, "计算机编程老师"), (2, "网管")]
    ]
    result = ScanResult()
    _accept_jobs(jobs, result, positives=[], negatives=[], include_non_canada=False,
                 known_urls=set(), known_company_roles=set(), apply=False, require_positive=False)
    assert result.new_jobs == 2
    assert result.skipped_duplicates == 0
