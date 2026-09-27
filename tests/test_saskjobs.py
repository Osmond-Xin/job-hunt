"""Tier 4b: SaskJobs.ca, filtered by NOC code rather than by title.

The row markup is trimmed from the live listing of 2026-09-27, including the
Job Bank mirror icon some rows carry in their last cell.
"""

from __future__ import annotations

import httpx

from job_hunt.services.saskjobs import parse_saskjobs, scan_saskjobs_source


def _row(job_id: int, title: str, noc: str, employer="Felix Homes", city="WEYBURN", date="26-Sep-2026") -> str:
    return f"""
              <tr>
                <td width="35%" valign="top" class="border_bottom" style="height: 35px; padding: 4px;"><a href="/jsp/joborder/detail.jsp?job_order_id={job_id}">{title}</a><br/>
                  <span style="font-size: 90%;">NOC {noc}</span></td>
                <td width="25%" valign="top" class="border_bottom" style="padding: 4px;">{employer}</td>
                <td width="17%" valign="top" class="border_bottom" style="padding: 4px;">{city}</td>
                <td width="15%" valign="top" class="border_bottom" style="padding: 4px;"><span style="white-space: nowrap;">{date}</span></td>
                <td width="4%" valign="top" class="border_bottom" align="right" style="padding: 4px; padding-right: 2px;">&nbsp;&nbsp;1</td>
                <td width="4%" valign="top" class="border_bottom" style="padding: 4px; padding-left: 2px; padding-right: 0px;">
                  <img class="njbimage" src="/images/jobsource-jobbank-16-icon-small.png" alt="National Job Bank"/>
                </td>
              </tr>"""


PAGE_2X = _row(1547682, "2nd Class Chief Engineer", "21399", "Saskatchewan Health Authority", "PRINCE ALBERT") + _row(
    1547700, "Business Systems Analyst", "21221", "Farm Credit &amp; Co", "REGINA", "27-Sep-2026"
)


def _scan(handler, codes=("21221", "21232"), **config):
    return scan_saskjobs_source(
        {"enabled": True, "delay_s": 0, **config},
        list(codes),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_a_row_carries_employer_town_noc_and_date():
    rows = parse_saskjobs(PAGE_2X)
    assert len(rows) == 2
    assert rows[1] == {
        "url": "https://www.saskjobs.ca/jsp/joborder/detail.jsp?job_order_id=1547700",
        "title": "Business Systems Analyst",
        "company": "Farm Credit & Co",
        "location": "Regina, Saskatchewan",
        "noc": "21221",
        "posted": "2026-09-27",
    }


def test_only_the_broad_categories_of_the_configured_codes_are_walked_and_codes_filter_rows():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        return httpx.Response(200, text=PAGE_2X if params["page"] == "1" else "")

    result = _scan(handler, codes=("21221", "22221", "11100"))

    # Category 1 ends on its empty page 2; category 2's first page repeats
    # rows already seen, which ends its walk too.
    assert [(p["noc_skill_type"], p["page"]) for p in seen] == [("1", "1"), ("1", "2"), ("2", "1")]
    assert seen[0]["filter_by"] == "2" and seen[0]["catsort"] == "0"
    # Returned once, and only the row whose code is on the list.
    assert [p.title for p in result.postings] == ["Business Systems Analyst"]
    assert result.postings[0].source == "saskjobs NOC 21221"
    assert result.health.ok and not result.health.truncated


def test_the_page_budget_running_out_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, text=_row(page, f"Developer {page}", "21232"))

    result = _scan(handler, max_pages=3)
    assert len(result.postings) == 3
    assert result.health.truncated
    assert any("raise max_pages" in w for w in result.health.warnings())


def test_a_failed_request_or_unparseable_page_is_an_error_not_a_quiet_board():
    down = _scan(lambda request: httpx.Response(503))
    assert down.postings == [] and down.health.errors == 1

    redesigned = _scan(lambda request: httpx.Response(200, text="<table>new markup</table>"))
    assert redesigned.postings == [] and redesigned.health.errors == 1


def test_disabled_or_no_codes_does_not_touch_the_network():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not hit the network")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    disabled = scan_saskjobs_source({"enabled": False}, ["21221"], client=client)
    assert disabled.postings == [] and disabled.health.ok


def test_enabled_with_no_noc_codes_is_unhealthy_and_says_why():
    """Review 2026-09-27: this returned ok with nothing, like a quiet week."""
    from job_hunt.services.scan import _source_scanned_jobs

    result = scan_saskjobs_source({"enabled": True}, [])
    assert result.postings == [] and not result.health.ok
    warnings: list[str] = []
    _source_scanned_jobs("saskjobs", lambda cfg: scan_saskjobs_source(cfg, None), {"enabled": True}, warnings)
    assert any("noc_codes is empty" in w for w in warnings)


def test_each_category_is_paged_on_its_own_rows():
    """Distinct rows per category, so a broken category-2 walk cannot hide
    behind rows category 1 already returned."""
    pages = {
        ("1", "1"): _row(1, "Accountant", "11100"),
        ("2", "1"): _row(21, "Developer A", "21232"),
        ("2", "2"): _row(22, "Developer B", "21232"),
    }
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.url.params["noc_skill_type"], request.url.params["page"])
        seen.append(key)
        return httpx.Response(200, text=pages.get(key, ""))

    result = _scan(handler, codes=("21232", "11100"))
    assert seen == [("1", "1"), ("1", "2"), ("2", "1"), ("2", "2"), ("2", "3")]
    assert sorted(p.title for p in result.postings) == ["Accountant", "Developer A", "Developer B"]
