"""Tier 17: Built In Vancouver. The card is trimmed from the live page of
2026-09-27."""

from __future__ import annotations

import httpx

from job_hunt.services.builtin import parse_cards, scan_builtin_source


def _card(i: int, place: str = "Richmond, BC, CAN") -> str:
    return (
        f'<div data-id="job-card" x-data="jobTracking"><a href="/company/boeing" data-id="company-title" '
        f'class="font-barlow"><span>Boeing</span></a><h2><a href="/job/senior-bsa/{i}" target="_blank" '
        f'data-id="job-card-title" class="card-alias">Senior Business Systems Analyst {i}</a></h2>'
        f'<span class="fs-xs"><i class="fa-regular fa-clock"></i>2 Days Ago</span>'
        f'<div><i class="fa-regular fa-location-dot fs-xs"></i></div><div><span class="font-barlow">{place}</span></div></div>'
    )


def _scan(handler, **config):
    return scan_builtin_source(
        {"enabled": True, "delay_s": 0, **config},
        ["Data Analyst"],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )


def test_a_card_carries_company_title_link_place_and_age():
    [row] = parse_cards(_card(11385206))
    assert row["url"] == "https://www.builtinvancouver.org/job/senior-bsa/11385206"
    assert row["title"] == "Senior Business Systems Analyst 11385206"
    assert row["company"] == "Boeing" and row["location"] == "Richmond, BC, CAN"
    assert row["posted"]  # "2 Days Ago" becomes a date


def test_a_short_page_ends_the_search():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        count = 25 if params["page"] == "1" else 3
        start = 0 if params["page"] == "1" else 25
        return httpx.Response(200, text="".join(_card(start + i) for i in range(count)))

    result = _scan(handler, max_pages=5)
    assert [p["page"] for p in seen] == ["1", "2"] and seen[0]["search"] == "Data Analyst"
    assert len(result.postings) == 28 and result.health.ok and not result.health.truncated


def test_the_budget_running_out_is_truncation():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, text="".join(_card(page * 25 + i) for i in range(25)))

    assert _scan(handler, max_pages=2).health.truncated


def test_no_cards_on_any_search_is_an_error_not_a_quiet_board():
    result = _scan(lambda request: httpx.Response(200, text="<html>redesigned</html>"))
    assert result.health.errors == 1 and "markup" in result.health.note
