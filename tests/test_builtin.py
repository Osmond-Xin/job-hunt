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
    assert result.health.errors == 1


# --- review of the new-sources batch, 2026-09-27 ------------------------------


def test_an_explicitly_empty_search_is_quiet_and_a_blocked_later_page_is_an_error():
    """H4: a 200 block page on page 2 ended the walk as if it were the end."""
    empty = _scan(lambda request: httpx.Response(200, text="<h2>No Results Found</h2>"))
    assert empty.health.ok and empty.postings == []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(200, text="".join(_card(i) for i in range(25)))
        return httpx.Response(200, text="<html>Just a moment...</html>")

    blocked = _scan(handler, max_pages=3)
    assert len(blocked.postings) == 25 and blocked.health.errors == 1


def test_a_card_that_does_not_parse_is_loss_and_does_not_end_the_walk():
    """H4: one unparseable card made a full page look short."""
    broken = '<div data-id="job-card"><span>redesigned card</span></div>'

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(200, text="".join(_card(i) for i in range(24)) + broken)
        return httpx.Response(200, text="".join(_card(100 + i) for i in range(3)))

    result = _scan(handler, max_pages=5)
    assert len(result.postings) == 27
    assert result.health.incomplete and any("1 job card" in w for w in result.health.warnings())


def test_title_link_in_any_attribute_order_company_span_with_class_and_ages():
    card = (
        '<div data-id="job-card"><a data-id="company-title" href="/company/x"><span class="name">Acme</span></a>'
        '<a data-id="job-card-title" class="card" href="/job/analyst/9">Analyst</a>'
        '<span>Reposted One Month Ago</span></div>'
        '<div data-id="job-card"><a href="/job/dev/8" data-id="job-card-title">Dev</a><span>30+ Days Ago</span></div>'
    )
    rows = parse_cards(card)
    assert [(r["url"], r["company"]) for r in rows] == [
        ("https://www.builtinvancouver.org/job/analyst/9", "Acme"),
        ("https://www.builtinvancouver.org/job/dev/8", "")]
    assert rows[0]["posted"] and rows[1]["posted"]



def test_a_card_with_a_script_link_or_no_title_is_loss_not_a_posting():
    """Round 9: href="javascript:void(0)" became a posting; an empty title
    vanished without being counted."""
    bad = ('<div data-id="job-card"><a data-id="job-card-title" href="javascript:void(0)">Analyst</a></div>'
           '<div data-id="job-card"><a data-id="job-card-title" href="/job/x/1"></a></div>'
           "<div data-id=\"job-card\"><a data-id='job-card-title' href='/job/y/2'>Developer</a></div>")
    result = _scan(lambda request: httpx.Response(200, text=bad))
    assert [p.url for p in result.postings] == ["https://www.builtinvancouver.org/job/y/2"]
    assert any("2 job card" in w for w in result.health.warnings())



def test_single_quoted_card_markers_are_counted_and_split():
    """Round 10: the card marker assumed double quotes."""
    cards = "".join(f"<div data-id='job-card'><a data-id='job-card-title' href='/job/a/{i}'>Analyst {i}</a></div>"
                    for i in range(3))
    result = _scan(lambda request: httpx.Response(200, text=cards))
    assert len(result.postings) == 3 and result.health.ok and not result.health.incomplete
