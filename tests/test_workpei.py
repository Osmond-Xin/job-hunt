"""Tier 12: WorkPEI through the board's WordPress REST API.

Items are trimmed from the live response of 2026-09-27, one of each kind:
a row posted on WorkPEI itself and a syndicated PEI public-service row.
"""

from __future__ import annotations

import httpx

from job_hunt.services.workpei import API, parse_listing, scan_workpei_source

OWN_ROW = {
    "id": 467296,
    "date": "2026-09-26T14:02:11",
    "link": "https://workpei.ca/job/government-of-pei-summerside-4477-bilingual-administrative-assistant/",
    "title": {"rendered": "Bilingual Administrative Assistant"},
    "meta": {"_company_name": "Government of PEI", "_filled": 0},
    "class_list": ["post-467296", "job_listing", "source-workpei"],
}
PUBLIC_SERVICE_ROW = {
    "id": 459087,
    "date": "2026-09-27T05:01:50",
    "link": "https://atlas.workland.com/work/77612/172753-accessability-supports-coordinator",
    "title": {"rendered": "172753 &#8211; AccessAbility Supports Coordinator &#8211; "
                          "Department of Social Development &#8211; Permanent"},
    "meta": {"_company_name": "", "_filled": 0},
    "class_list": ["post-459087", "job_listing", "source-pei-public-service"],
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_workpei_row_keeps_its_employer_and_date():
    row = parse_listing(OWN_ROW)
    assert row["title"] == "Bilingual Administrative Assistant"
    assert row["company"] == "Government of PEI"
    assert row["location"] == "Prince Edward Island"
    assert row["posted"] == "2026-09-26"


def test_a_public_service_row_is_split_into_role_and_employer():
    row = parse_listing(PUBLIC_SERVICE_ROW)
    assert row["title"] == "AccessAbility Supports Coordinator"
    assert row["company"] == "Government of PEI"
    assert row["url"].startswith("https://atlas.workland.com/")


def test_a_filled_posting_is_dropped():
    assert parse_listing({**OWN_ROW, "meta": {"_filled": 1}}) is None


def test_the_filled_flag_is_read_whatever_its_serialised_type():
    """Review 2026-09-27: bool("0") is True, so a string flag would have
    dropped every open posting."""
    for flag in ("1", True, "true"):
        assert parse_listing({**OWN_ROW, "meta": {"_filled": flag}}) is None
    for flag in ("0", 0, False, "", None):
        assert parse_listing({**OWN_ROW, "meta": {"_filled": flag}}) is not None


def test_markup_in_a_rendered_title_is_stripped():
    row = parse_listing({**OWN_ROW, "title": {"rendered": "<em>Data</em> Analyst &amp; Developer"}})
    assert row["title"] == "Data Analyst & Developer"


def test_the_sweep_stops_at_the_advertised_page_count():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        body = [OWN_ROW] if params["page"] == "1" else [PUBLIC_SERVICE_ROW]
        return httpx.Response(200, json=body, headers={"X-WP-Total": "2", "X-WP-TotalPages": "2"})

    result = scan_workpei_source({"enabled": True, "delay_s": 0}, client=_client(handler), sleep=lambda _s: None)

    assert [p["page"] for p in seen] == ["1", "2"]
    assert seen[0]["per_page"] == "100" and "title" in seen[0]["_fields"]
    assert [p.title for p in result.postings] == [
        "Bilingual Administrative Assistant", "AccessAbility Supports Coordinator"]
    assert result.health.advertised == 2 and not result.health.truncated and result.health.ok


def test_more_pages_than_the_budget_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{**OWN_ROW, "link": f"https://workpei.ca/job/{request.url.params['page']}"}],
                              headers={"X-WP-Total": "271", "X-WP-TotalPages": "3"})

    result = scan_workpei_source(
        {"enabled": True, "delay_s": 0, "max_pages": 2}, client=_client(handler), sleep=lambda _s: None
    )
    assert len(result.postings) == 2
    assert result.health.truncated
    assert any("advertises 271" in w for w in result.health.warnings())


def test_a_failed_request_is_an_error_not_an_empty_board():
    result = scan_workpei_source(
        {"enabled": True}, client=_client(lambda request: httpx.Response(500)), sleep=lambda _s: None
    )
    assert result.postings == [] and result.health.errors == 1


# --- review round 1, 2026-09-27 ----------------------------------------------

_HEADERS = {"X-WP-Total": "271", "X-WP-TotalPages": "3"}


def test_a_200_whose_body_is_not_the_list_is_an_error():
    result = scan_workpei_source(
        {"enabled": True, "delay_s": 0},
        client=_client(lambda request: httpx.Response(200, json={"message": "down"}, headers=_HEADERS)),
        sleep=lambda _s: None,
    )
    assert result.postings == [] and result.health.errors == 1 and not result.health.ok


def test_an_empty_page_before_the_last_advertised_one_is_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        body = [OWN_ROW] if request.url.params["page"] == "1" else []
        return httpx.Response(200, json=body, headers=_HEADERS)

    result = scan_workpei_source({"enabled": True, "delay_s": 0}, client=_client(handler), sleep=lambda _s: None)
    assert result.health.incomplete and not result.health.truncated
    assert any("ended short" in w for w in result.health.warnings())


def test_an_early_empty_page_is_incomplete_not_truncated_even_past_the_budget():
    """Round 2: TotalPages above max_pages made an empty first page read as a
    budget problem; only a walk the budget actually stopped is truncated."""
    result = scan_workpei_source(
        {"enabled": True, "delay_s": 0, "max_pages": 6},
        client=_client(lambda request: httpx.Response(
            200, json=[], headers={"X-WP-Total": "1000", "X-WP-TotalPages": "10"})),
        sleep=lambda _s: None,
    )
    assert result.health.incomplete and not result.health.truncated


def test_a_failed_read_is_one_warning_not_two():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=[OWN_ROW], headers=_HEADERS)
        return httpx.Response(500)

    result = scan_workpei_source({"enabled": True, "delay_s": 0}, client=_client(handler), sleep=lambda _s: None)
    assert result.health.errors == 1 and not result.health.incomplete
    assert len(result.health.warnings()) == 1


def test_filled_postings_count_toward_the_advertised_total():
    """A filled posting is dropped on purpose; it must not read as a missed row."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[OWN_ROW, {**OWN_ROW, "link": "https://workpei.ca/job/f",
                                                   "meta": {"_filled": 1}}],
                              headers={"X-WP-Total": "2", "X-WP-TotalPages": "1"})

    result = scan_workpei_source({"enabled": True, "delay_s": 0}, client=_client(handler), sleep=lambda _s: None)
    assert len(result.postings) == 1
    assert not result.health.incomplete and result.health.warnings() == []


def test_the_request_goes_to_the_documented_endpoint():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url).split("?")[0])
        return httpx.Response(200, json=[], headers={"X-WP-TotalPages": "1"})

    scan_workpei_source({"enabled": True}, client=_client(handler), sleep=lambda _s: None)
    assert urls == [API]
