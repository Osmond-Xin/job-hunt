"""Tier 11: single-employer career sites on Oracle, Jibe and UKG.

Fixtures are trimmed from the live responses of 2026-09-27: J.D. Irving
(Oracle), City of Thunder Bay (Jibe) and Yukon Hospitals (UKG).
"""

from __future__ import annotations

import json

import httpx

from job_hunt.services.employer_boards import scan_employer_boards_source

JDI_URL = "https://hcpd.fa.ca2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_5003/requisitions"
THUNDER_BAY_URL = "https://careers.thunderbay.ca/careers-home/jobs"
YUKON_URL = (
    "https://recruiting.ultipro.ca/YUK5001YUHC/JobBoard/"
    "7ed94cc1-3925-495d-a225-c5d6b7c3ad3e/?q=&o=postedDateDesc"
)

JIBE_PAYLOAD = {
    "totalCount": 1,
    "count": 1,
    "jobs": [{"data": {
        "slug": "7172", "req_id": "7172",
        "title": "Transit Operator (Specialized & Conventional) (Part-Time)",
        "city": "Thunder Bay", "state": "Ontario", "country": "Canada",
        "posted_date": "2026-09-25T04:01:00+0000",
        "hiring_organization": "City of Thunder Bay",
    }}],
}

UKG_PAYLOAD = {
    "totalCount": 1,
    "opportunities": [{
        "Id": "8f681c67-d7a8-4265-855a-1a7c17514613",
        "Title": "Laboratory Assistant - Laboratory Services - Regular Part-Time",
        "Locations": [{"LocalizedName": "Whitehorse General Hospital", "Address": {
            "City": "Whitehorse", "State": {"Code": "YT", "Name": "Yukon"},
            "Country": {"Code": "CAN", "Name": "Canada"}}}],
        "PostedDate": "2026-09-25T16:50:23.649Z",
    }],
}


def _oracle_page(ids, total):
    return {"items": [{"TotalJobsCount": total, "requisitionList": [
        {"Id": str(i), "Title": f"Maintenance Planner {i}", "PrimaryLocation": "Saint John, NB, Canada",
         "PostedDate": "2026-09-25", "PostingEndDate": None} for i in ids]}]}


def _config(*employers, **overrides):
    config = {"enabled": True, "delay_s": 0, "max_pages": 5, "employers": list(employers)}
    config.update(overrides)
    return config


def _scan(config, handler):
    return scan_employer_boards_source(
        config, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None
    )


def test_a_disabled_tier_returns_nothing_and_stays_healthy():
    result = scan_employer_boards_source({"enabled": False})
    assert result.postings == [] and result.health.ok


def test_an_oracle_employer_is_read_to_its_advertised_total():
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        finder = request.url.params["finder"]
        offset = int(finder.split("offset=")[1].split(",")[0])
        offsets.append(offset)
        assert "siteNumber=CX_5003," in finder and "limit=200," in finder
        ids = range(offset, min(offset + 200, 250))
        return httpx.Response(200, json=_oracle_page(ids, 250))

    result = _scan(_config({"name": "J.D. Irving", "platform": "oracle", "url": JDI_URL}), handler)

    assert offsets == [0, 200]
    assert len(result.postings) == 250
    posting = result.postings[0]
    assert posting.company == "J.D. Irving"
    assert posting.url == "https://hcpd.fa.ca2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_5003/job/0"
    assert posting.location == "Saint John, NB, Canada" and posting.posted == "2026-09-25"
    assert posting.portal == "oracle"
    assert result.health.advertised == 250 and not result.health.truncated


def test_a_jibe_site_is_read_through_its_api():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=JIBE_PAYLOAD)

    result = _scan(_config({"name": "City of Thunder Bay", "platform": "jibe", "url": THUNDER_BAY_URL}), handler)

    assert seen[0].path == "/api/jobs" and seen[0].params["page"] == "1" and seen[0].params["limit"] == "100"
    assert len(seen) == 1  # totalCount reached on the first page
    posting = result.postings[0]
    assert posting.url == "https://careers.thunderbay.ca/jobs/7172"
    assert posting.location == "Thunder Bay, Ontario, Canada"
    assert posting.posted == "2026-09-25"
    assert posting.company == "City of Thunder Bay"


def test_a_ukg_board_is_read_through_its_search_post():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=UKG_PAYLOAD)

    result = _scan(_config({"name": "Yukon Hospitals", "platform": "ukg", "url": YUKON_URL}), handler)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://recruiting.ultipro.ca/YUK5001YUHC/JobBoard/"
        "7ed94cc1-3925-495d-a225-c5d6b7c3ad3e/JobBoardView/LoadSearchResults"
    )
    assert json.loads(request.content)["opportunitySearch"]["Skip"] == 0
    posting = result.postings[0]
    assert posting.url.endswith("/OpportunityDetail?opportunityId=8f681c67-d7a8-4265-855a-1a7c17514613")
    assert posting.location == "Whitehorse, Yukon, Canada"
    assert posting.posted == "2026-09-25"


def test_the_page_budget_running_out_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["finder"].split("offset=")[1].split(",")[0])
        return httpx.Response(200, json=_oracle_page(range(offset, offset + 200), 736))

    result = _scan(
        _config({"name": "J.D. Irving", "platform": "oracle", "url": JDI_URL}, max_pages=2), handler
    )
    assert len(result.postings) == 400
    assert result.health.truncated
    assert any("raise max_pages" in w and "736" in w for w in result.health.warnings())


def test_an_unreadable_entry_or_a_failed_request_is_an_error_not_an_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    result = _scan(
        _config(
            {"name": "Typo", "platform": "workday", "url": JDI_URL},
            {"name": "No site", "platform": "oracle", "url": "https://example.com/careers"},
            {"name": "Down", "platform": "jibe", "url": THUNDER_BAY_URL},
        ),
        handler,
    )
    assert result.postings == []
    assert result.health.errors == 3
    assert any("failed request" in w for w in result.health.warnings())


# hrSmart: the two tenants order their columns differently (trimmed 2026-09-27).
YUKON_GOV_ROW = """<tr>
 <td class="tdhighlight">Job Opportunity</td>
 <td class="tdhighlight">69353</td>
 <td class="tdhighlight"><a href="/hr/ats/Posting/view/69353"> <i class="fas fa-star"></i> <span>Registered Nurse Supervisor</span>
 </a></td>
 <td class="tdhighlight">Health &amp; Social Services - Administration - Mcdonald Lodge</td>
 <td class="tdhighlight"> Dawson City, YT, CA Y0B 1G0<br />
 </td>
 <td class="tdhighlight">23 December 2026</td>
</tr>"""
BC_ROW = """<tr> <td>Attorney General</td>
 <td><a href="/hr/ats/Posting/view/{id}"> <span>Data Analyst R{id}</span>
 </a></td>
 <td>GEU</td>
 <td>On-Site</td>
 <td> Abbotsford, BC, CA V2S 1H4<br />
 </td>
 <td>9/25/2026</td>
</tr>"""
HEADER_ROW = '<tr><th scope="col">Req. #</th><th scope="col">Job Title</th></tr>'


def test_an_hrsmart_row_is_read_whatever_the_column_order():
    from job_hunt.services.employer_boards import parse_hrsmart

    site = "https://yukongovernment.hua.hrsmart.com"
    assert parse_hrsmart(HEADER_ROW + YUKON_GOV_ROW, site) == [{
        "url": f"{site}/hr/ats/Posting/view/69353",
        "title": "Registered Nurse Supervisor",
        "location": "Dawson City, YT, Canada",
    }]
    bc = parse_hrsmart(BC_ROW.format(id=126069), "https://bcpublicservice.hua.hrsmart.com")
    assert bc[0]["title"] == "Data Analyst R126069" and bc[0]["location"] == "Abbotsford, BC, Canada"


def test_an_hrsmart_board_is_walked_until_a_page_repeats():
    paths: list[str] = []
    pages = {
        "/hr/ats/JobSearch/viewAll": "".join(BC_ROW.format(id=i) for i in range(25)),
        "/hr/ats/JobSearch/viewAll/jobSearchPaginationExternal_page:2": BC_ROW.format(id=99),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        # Past the end the site serves its last page again.
        return httpx.Response(200, text=pages.get(request.url.path, pages[paths[1]] if len(paths) > 1 else ""))

    result = _scan(
        _config({"name": "BC Public Service", "platform": "hrsmart",
                 "url": "https://bcpublicservice.hua.hrsmart.com/hr/ats/JobSearch/viewAll"}),
        handler,
    )
    assert paths == [
        "/hr/ats/JobSearch/viewAll",
        "/hr/ats/JobSearch/viewAll/jobSearchPaginationExternal_page:2",
        "/hr/ats/JobSearch/viewAll/jobSearchPaginationExternal_page:3",
    ]
    assert len(result.postings) == 26
    assert result.postings[0].company == "BC Public Service" and result.postings[0].portal == "hrsmart"
    assert result.health.ok and not result.health.truncated


def test_an_hrsmart_first_page_with_no_rows_is_an_error():
    result = _scan(
        _config({"name": "Government of Yukon", "platform": "hrsmart",
                 "url": "https://yukongovernment.hua.hrsmart.com/"}),
        lambda request: httpx.Response(200, text="<html>redesigned</html>"),
    )
    assert result.postings == [] and result.health.errors == 1


# --- review round 1, 2026-09-27 ----------------------------------------------


def test_a_repeated_page_below_the_advertised_total_is_incomplete():
    """250 advertised, 100 returned, then the same 100 again: the walk stops
    on the repeat, but the missing 150 must be said, not read as healthy."""
    page = {"totalCount": 250, "jobs": [
        {"data": {"slug": str(i), "title": f"Analyst {i}"}} for i in range(100)]}
    result = _scan(
        _config({"name": "City of Thunder Bay", "platform": "jibe", "url": THUNDER_BAY_URL}),
        lambda request: httpx.Response(200, json=page),
    )
    assert len(result.postings) == 100
    assert result.health.incomplete and not result.health.truncated
    assert any("ended short" in w and "250" in w for w in result.health.warnings())


def test_a_complete_board_is_not_flagged_incomplete():
    result = _scan(
        _config({"name": "City of Thunder Bay", "platform": "jibe", "url": THUNDER_BAY_URL}),
        lambda request: httpx.Response(200, json=JIBE_PAYLOAD),
    )
    assert not result.health.incomplete and result.health.warnings() == []


def test_a_200_without_the_job_list_is_an_error_on_every_platform():
    malformed = {"message": "maintenance"}
    result = _scan(
        _config(
            {"name": "J.D. Irving", "platform": "oracle", "url": JDI_URL},
            {"name": "City of Thunder Bay", "platform": "jibe", "url": THUNDER_BAY_URL},
            {"name": "Yukon Hospitals", "platform": "ukg", "url": YUKON_URL},
        ),
        lambda request: httpx.Response(200, json=malformed),
    )
    assert result.postings == [] and result.health.errors == 3


def test_a_ukg_posting_is_placed_by_its_canadian_location_when_it_lists_several():
    boston = {"Address": {"City": "Boston", "State": {"Name": "Massachusetts"},
                          "Country": {"Code": "USA", "Name": "United States"}}}
    toronto = {"Address": {"City": "Toronto", "State": {"Name": "Ontario"},
                           "Country": {"Code": "CAN", "Name": "Canada"}}}
    payload = {"totalCount": 1, "opportunities": [
        {**UKG_PAYLOAD["opportunities"][0], "Locations": [boston, toronto]}]}
    result = _scan(
        _config({"name": "Yukon Hospitals", "platform": "ukg", "url": YUKON_URL}),
        lambda request: httpx.Response(200, json=payload),
    )
    assert result.postings[0].location == "Toronto, Ontario, Canada"


# --- review round 2, 2026-09-27 ----------------------------------------------


def test_a_later_page_without_a_total_keeps_the_first_one():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(200, json={"totalCount": 250, "jobs": [
                {"data": {"slug": str(i), "title": "Analyst"}} for i in range(100)]})
        return httpx.Response(200, json={"jobs": []})  # no totalCount

    result = _scan(_config({"name": "City of Thunder Bay", "platform": "jibe", "url": THUNDER_BAY_URL}), handler)
    assert result.health.advertised == 250 and result.health.incomplete


def test_a_short_board_is_named_and_not_mixed_with_one_that_has_no_total():
    """An hrSmart board (no total) next to a short Jibe board read "60
    collected of 20 advertised"; the warning now names the short employer."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("hrsmart.com"):
            # Every page is the same 50 rows, so page 2 repeats and the walk ends.
            return httpx.Response(200, text="".join(BC_ROW.format(id=i) for i in range(50)))
        return httpx.Response(200, json={"totalCount": 20, "jobs": [
            {"data": {"slug": str(i), "title": "Analyst"}} for i in range(10)]})

    result = _scan(
        _config(
            {"name": "BC Public Service", "platform": "hrsmart",
             "url": "https://bcpublicservice.hua.hrsmart.com/hr/ats/JobSearch/viewAll"},
            {"name": "City of Thunder Bay", "platform": "jibe", "url": THUNDER_BAY_URL},
        ),
        handler,
    )
    [warning] = result.health.warnings()
    assert "City of Thunder Bay 10 of 20" in warning and "BC Public Service" not in warning


def test_an_oracle_item_without_its_requisition_list_is_an_error():
    result = _scan(
        _config({"name": "J.D. Irving", "platform": "oracle", "url": JDI_URL}),
        lambda request: httpx.Response(200, json={"items": [{"message": "maintenance"}]}),
    )
    assert result.postings == [] and result.health.errors == 1


def test_an_empty_oracle_search_is_a_quiet_board_not_an_error():
    """Shape of a real zero-result search (NB, 2026-09-27)."""
    result = _scan(
        _config({"name": "J.D. Irving", "platform": "oracle", "url": JDI_URL}),
        lambda request: httpx.Response(200, json={"items": [{"TotalJobsCount": 0, "requisitionList": []}]}),
    )
    assert result.health.ok and result.health.warnings() == []


def test_a_ukg_canadian_location_is_recognised_however_the_country_is_written():
    from job_hunt.services.employer_boards import _ukg_location

    boston = {"Address": {"City": "Boston", "Country": {"Code": "USA", "Name": "United States"}}}
    for country in ({"Code": "can"}, {"Code": "", "Name": "Canada"}):
        toronto = {"Address": {"City": "Toronto", "State": {"Name": "Ontario"}, "Country": country}}
        assert _ukg_location([boston, toronto]).startswith("Toronto, Ontario")


# --- 2026-09-27 source wiring -------------------------------------------------

SF_PAGE = """<span class="paginationLabel">Results <b>1 &ndash; 2</b> of <b>2</b></span>
<tr class="data-row"><td><span class="jobTitle hidden-phone">
<a href="/job/Regina-Data-Analyst/606439117/" class="jobTitle-link">Data Analyst</a></span>
<span class="jobLocation">REGINA, SK, CA</span></td></tr>
<tr class="data-row"><td><span class="jobTitle hidden-phone">
<a href="/job/Regina-Lineperson/606439118/" class="jobTitle-link">Powerline Technician</a></span>
<span class="jobLocation">REGINA, SK, CA</span></td></tr>"""


def test_a_successfactors_employer_is_read_to_its_advertised_total():
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(request.url.params["startrow"])
        return httpx.Response(200, text=SF_PAGE)

    result = _scan(_config({"name": "SaskPower", "platform": "successfactors",
                            "url": "https://jobs.saskpower.com/"}), handler)
    assert starts == ["0"]  # 2 of 2 read on the first page
    assert [p.url for p in result.postings] == [
        "https://jobs.saskpower.com/job/Regina-Data-Analyst/606439117/",
        "https://jobs.saskpower.com/job/Regina-Lineperson/606439118/"]
    assert result.postings[0].company == "SaskPower" and result.postings[0].location == "REGINA, SK, CA"
    assert result.health.advertised == 2 and result.health.ok


def test_a_successfactors_page_that_no_longer_parses_is_an_error():
    result = _scan(_config({"name": "SaskTel", "platform": "successfactors",
                            "url": "https://jobs.sasktel.com"}),
                   lambda request: httpx.Response(200, text="<html>redesigned</html>"))
    assert result.postings == [] and result.health.errors == 1


def test_an_employer_can_carry_its_own_page_budget():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        page = len(calls)
        return httpx.Response(200, text="".join(BC_ROW.format(id=page * 100 + i) for i in range(25)))

    result = _scan(_config({"name": "Island Health", "platform": "hrsmart",
                            "url": "https://islandhealth.hua.hrsmart.com/", "max_pages": 4},
                           max_pages=2), handler)
    assert len(calls) == 4 and len(result.postings) == 100 and result.health.truncated


# Trimmed from careers.niagarahealth.on.ca/erecruit/RSS.aspx, 2026-09-27: the
# feed is not well-formed XML, and the postings follow an empty <item/>.
ERECRUIT_FEED = """<?xml version="1.0" encoding="Windows-1252"?><rss version="2.0"><channel>
<item /></channel></rss><main><div id="pnlRSS"><title>Careers at Niagara Health</title>
<link>https://careers.niagarahealth.on.ca/eRecruit </link><ttl>60 </ttl>
<h1>SR CASE COSTING PERFORMANCE ADVISOR, FT      </h1><description><B>VACANCY DETAILS</B><br><br>
VACANCY NUMBER: 44060<br><br>LOCATION: ST. CATHARINES SITE / HYBRID <br><br></description>
<link>https://careers.niagarahealth.on.ca/eRecruit/VacancyDetail.aspx?VacancyUID=000000041084 </link>
<pubDate>Tue, 18 Aug 2026 10:00:34 GMT </pubDate>
<h1>REGISTERED NURSE      </h1><description>LOCATION: NIAGARA HEALTH <br></description>
<link>https://careers.niagarahealth.on.ca/eRecruit/VacancyDetail.aspx?VacancyUID=000000039887 </link>
<pubDate>Mon, 16 Mar 2026 10:00:34 GMT </pubDate></div></main>"""


def test_an_erecruit_feed_is_read_as_its_h1_sequence_with_a_location_hint():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, text=ERECRUIT_FEED)

    result = _scan(_config({"name": "Niagara Health", "platform": "erecruit",
                            "url": "https://careers.niagarahealth.on.ca/erecruit",
                            "location_hint": "Niagara, ON"}), handler)
    assert paths == ["/erecruit/RSS.aspx"]  # one feed, no paging
    first = result.postings[0]
    assert first.title == "SR CASE COSTING PERFORMANCE ADVISOR, FT"
    assert first.url.endswith("VacancyUID=000000041084")
    assert first.location == "ST. CATHARINES SITE / HYBRID, Niagara, ON"
    assert first.posted == "2026-08-18"
    assert len(result.postings) == 2 and result.health.ok and not result.health.incomplete


def test_an_erecruit_feed_with_no_postings_is_an_error():
    result = _scan(_config({"name": "Niagara Health", "platform": "erecruit",
                            "url": "https://careers.niagarahealth.on.ca/erecruit"}),
                   lambda request: httpx.Response(200, text="<rss><channel><item /></channel></rss>"))
    assert result.health.errors == 1


# Trimmed from Niagara Region's Candidate Gateway, 2026-09-27.
PS_URL = ("https://careers.niagararegion.ca/psc/careers/EMPLOYEE/PSFT_HR/c/"
          "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL?FOCUS=Applicant&Siteid=1002")
PS_FORM = ("<form name='win0' method='post' action='https://careers.niagararegion.ca/psc/careers/"
           "EMPLOYEE/PSFT_HR/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL'>"
           "<input type='hidden' name='ICSID' id='ICSID' value='abc123' />"
           "<input type='hidden' name='ICStateNum' id='ICStateNum' value='1' /></form>")
PS_RESULTS = ("<div id='HRS_AGNT_RSLT_I$0'><ul>"
              "<li id='HRS_AGNT_RSLT_I$0_row_0'><span id='SCH_JOB_TITLE$0'>Senior Business Specialist IT</span>"
              "<span id='HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID$0'>46277</span>"
              "<span id='LOCATION$0'>Regional Headquarters</span><span id='SCH_OPENED$0'>09/25/2026</span></li>"
              "</ul></div>")


def test_a_peoplesoft_gateway_is_read_by_posting_its_form_back():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=PS_FORM if request.method == "GET" else PS_RESULTS)

    result = _scan(_config({"name": "Niagara Region", "platform": "peoplesoft", "url": PS_URL,
                            "location_hint": "Niagara, ON"}), handler)
    assert [r.method for r in requests] == ["GET", "POST"]
    form = dict(pair.split("=", 1) for pair in requests[1].content.decode().split("&"))
    assert form["ICAction"] == "NAV_PB%240" and form["ICSID"] == "abc123"
    [posting] = result.postings
    assert posting.title == "Senior Business Specialist IT"
    assert posting.url.endswith("?Page=HRS_APP_JBPST_FL&Action=U&FOCUS=Applicant"
                                "&SiteId=1002&JobOpeningId=46277&PostingSeq=1")
    assert posting.location == "Regional Headquarters, Niagara, ON" and posting.posted == "2026-09-25"
    assert result.health.ok


def test_a_peoplesoft_page_without_its_results_grid_is_an_error():
    result = _scan(_config({"name": "Niagara Region", "platform": "peoplesoft", "url": PS_URL}),
                   lambda request: httpx.Response(200, text=PS_FORM if request.method == "GET" else "<html/>"))
    assert result.health.errors == 1


# Trimmed from hhsc.taleo.net/careersection/2, 2026-09-27: two records of the
# "!|!"-joined list, the first in the page's initialHistory field.
TALEO_RECORD = ("!|!false!|!false!|!{id}!|!{title}!|!{id}!|!{title}!|!{id}!|!{id}!|!{id}!|!{id}!|!{id}"
                "!|!Full-time!|!125733!|!Ontario-Hamilton!|!false!|!!|!Hamilton General Hospital"
                "!|!OR PACU!|!CUPE 7800!|!Sep 26, 2026!|!Oct 2, 2026!|!Apply!|!")
TALEO_PAGE = ('<input type="hidden" name="listRequisition.nbElements" id="listRequisition.nbElements" value="2" />'
              '<input type="hidden" name="csrftoken" id="csrftoken" value="tok" />'
              '<input type="hidden" name="initialHistory" id="initialHistory" value="ftlx0!|!jobsearch'
              + TALEO_RECORD.format(id=762948, title="Data Analyst")
              + TALEO_RECORD.format(id=763032, title="RRT  - Respiratory Therapist") + '" />')


def test_a_taleo_classic_section_is_read_from_its_embedded_list():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=TALEO_PAGE)

    result = _scan(_config({"name": "Hamilton Health Sciences", "platform": "taleo",
                            "url": "https://hhsc.taleo.net/careersection/2/jobsearch.ftl",
                            "location_hint": "Hamilton, ON"}), handler)
    assert requests[0].url.params["dropListSize"] == "100" and len(requests) == 1
    first = result.postings[0]
    assert first.url == "https://hhsc.taleo.net/careersection/2/jobdetail.ftl?job=762948&lang=en"
    assert first.title == "Data Analyst" and first.location == "Hamilton, ON" and first.posted == "2026-09-26"
    assert len(result.postings) == 2 and result.health.advertised == 2 and not result.health.incomplete


def test_a_taleo_page_without_its_count_is_an_error():
    result = _scan(_config({"name": "HHS", "platform": "taleo",
                            "url": "https://hhsc.taleo.net/careersection/2/jobsearch.ftl"}),
                   lambda request: httpx.Response(200, text="<html>redirected</html>"))
    assert result.health.errors == 1


# Trimmed from Niagara Parks' ADP career centre, 2026-09-27.
ADP_URL = ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
           "?cid=e2b4f25a-a77b-4b3d-9657-b97ec357ea04")
ADP_REQ = {
    "requisitionTitle": "Enterprise Application Analyst", "postDate": "2026-09-22T09:13:00.000-04:00",
    "requisitionLocations": [
        {"address": {"cityName": "", "countrySubdivisionLevel1": {"codeValue": ""}}},
        {"address": {"cityName": "Niagara Falls", "countrySubdivisionLevel1": {"codeValue": "ON"}}}],
    "customFieldGroup": {"stringFields": [{"stringValue": "598380", "nameCode": {"codeValue": "ExternalJobID"}}]},
}


def test_an_adp_career_centre_is_read_by_company_id():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"jobRequisitions": [ADP_REQ], "meta": {"totalNumber": 1}})

    result = _scan(_config({"name": "Niagara Parks", "platform": "adp", "url": ADP_URL,
                            "location_hint": "Niagara Falls, ON"}), handler)
    assert seen[0]["cid"] == "e2b4f25a-a77b-4b3d-9657-b97ec357ea04" and seen[0]["$skip"] == "1"
    [posting] = result.postings
    assert posting.url.endswith("recruitment.html?cid=e2b4f25a-a77b-4b3d-9657-b97ec357ea04&jobId=598380")
    # The hint is not repeated when the location already names the town.
    assert posting.location == "Niagara Falls, ON" and posting.posted == "2026-09-22"
    assert result.health.advertised == 1 and result.health.ok


def test_an_adp_response_without_its_list_is_an_error():
    result = _scan(_config({"name": "Niagara Parks", "platform": "adp", "url": ADP_URL}),
                   lambda request: httpx.Response(200, json={"errors": ["x"]}))
    assert result.health.errors == 1


# Trimmed from the City of St. Catharines' Taleo Business Edition page, 2026-09-27.
TBE_URL = "https://tre.tbe.taleo.net/tre01/ats/careers/v2/searchResults?org=COSC&cws=37"
TBE_PAGE = """<div class="oracletaleocwsv2-accordion-head-info">
<h4 class="oracletaleocwsv2-head-title"><a href="https://tre.tbe.taleo.net/tre01/ats/careers/v2/viewRequisition?org=COSC&amp;cws=37&amp;rid=2978" class="viewJobLink">Arborist I</a></h4>
<div tabindex="0" >Community, Recreation and Culture Services</div></div>
<button data-href="https://tre.tbe.taleo.net/tre01/ats/careers/v2/viewRequisition?org=COSC&cws=37&rid=2978">Share</button>
<h4 class="oracletaleocwsv2-head-title"><a href="https://tre.tbe.taleo.net/tre01/ats/careers/v2/viewRequisition?org=COSC&amp;cws=37&amp;rid=3103" class="viewJobLink">Business Systems Analyst</a></h4>"""


def test_a_taleo_business_edition_page_is_read_in_one_request():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, text=TBE_PAGE)

    result = _scan(_config({"name": "City of St. Catharines", "platform": "tbe", "url": TBE_URL,
                            "location_hint": "St. Catharines, ON"}), handler)
    assert seen == [{"org": "COSC", "cws": "37", "act": "search", "maxRows": "500"}]
    assert [p.url for p in result.postings] == [
        "https://tre.tbe.taleo.net/tre01/ats/careers/v2/viewRequisition?org=COSC&cws=37&rid=2978",
        "https://tre.tbe.taleo.net/tre01/ats/careers/v2/viewRequisition?org=COSC&cws=37&rid=3103"]
    assert result.postings[1].location == "St. Catharines, ON" and result.health.ok


def test_a_tbe_page_that_is_not_the_careers_page_is_an_error():
    result = _scan(_config({"name": "City of St. Catharines", "platform": "tbe", "url": TBE_URL}),
                   lambda request: httpx.Response(200, text="<html>maintenance</html>"))
    assert result.health.errors == 1


# Trimmed from Mohawk College's Cornerstone site, 2026-09-27.
CSOD_URL = "https://talent-mohawkcollege.csod.com/ux/ats/careersite/2/home?c=talent-mohawkcollege"
CSOD_HOME = '<script>if (!csod.context  || !csod.context.token)  csod.context={"corp":"talent-mohawkcollege","token":"tok123"};</script>'
CSOD_REQ = {"requisitionId": 3432, "postingEffectiveDate": "9/25/2026",
            "displayJobTitle": "Business Systems Analyst",
            "locations": [{"city": "Hamilton", "state": "Ontario", "country": "CA"}]}


def test_a_cornerstone_site_is_searched_with_the_token_its_page_embeds():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, text=CSOD_HOME)
        return httpx.Response(200, json={"status": "Success",
                                         "data": {"totalCount": 1, "requisitions": [CSOD_REQ]}})

    result = _scan(_config({"name": "Mohawk College", "platform": "cornerstone", "url": CSOD_URL}), handler)
    search = requests[1]
    assert str(search.url) == "https://us.api.csod.com/rec-job-search/external/jobs"
    assert search.headers["authorization"] == "Bearer tok123"
    assert json.loads(search.content)["careerSiteId"] == 2
    [posting] = result.postings
    assert posting.url == ("https://talent-mohawkcollege.csod.com/ux/ats/careersite/2/home/"
                           "requisition/3432?c=talent-mohawkcollege")
    assert posting.location == "Hamilton, Ontario, CA" and posting.posted == "2026-09-25"
    assert result.health.advertised == 1 and result.health.ok


def test_a_cornerstone_page_without_a_token_is_an_error():
    result = _scan(_config({"name": "Mohawk College", "platform": "cornerstone", "url": CSOD_URL}),
                   lambda request: httpx.Response(200, text="<html>no context</html>"))
    assert result.health.errors == 1


# Trimmed from welland.myavanti.ca/careers/Job/Jobs, 2026-09-27.
AVANTI_ITEMS = {"items": [
    {"Job": "2026-54  ", "Title": "Design Technologist", "Location": "Engineering - Administration",
     "ClosingDate": "Oct 16, 2026", "ApplyUrl": None},
    {"Job": "2025     ", "Title": "Crossing Guard", "Location": "Crossing Guards", "ClosingDate": ""}]}


def test_an_avanti_site_lists_every_opening_in_one_post():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=AVANTI_ITEMS)

    result = _scan(_config({"name": "City of Welland", "platform": "avanti",
                            "url": "https://welland.myavanti.ca/careers", "location_hint": "Welland, ON"}), handler)
    assert [(r.method, r.url.path) for r in requests] == [("POST", "/careers/Job/Jobs")]
    first = result.postings[0]
    assert first.url == "https://welland.myavanti.ca/careers/Job/Details/2026-54"
    assert first.location == "Engineering - Administration, Welland, ON" and first.closes == "2026-10-16"
    assert len(result.postings) == 2 and result.health.ok


def test_an_avanti_response_without_items_is_an_error():
    result = _scan(_config({"name": "City of Welland", "platform": "avanti",
                            "url": "https://welland.myavanti.ca/careers"}),
                   lambda request: httpx.Response(200, json={"error": "x"}))
    assert result.health.errors == 1


# Trimmed from jobs.northernhealth.ca, 2026-09-27.
def _aspx_page(ids, start, total, next_link=True):
    rows = "".join(
        f'<tr id="rowItem" role="button"><td data-col="Position"><a href="/ViewJobPosting/{i}?ReturnUrl=x">'
        f'<span id="lblPosition" class="font-weight-bold">*Data Analyst {i}</span></a></td>'
        f'<td data-col="Location"><a href="/ViewJobPosting/{i}"> Prince George, BC </a></td></tr>'
        for i in ids)
    nxt = ("<a href=\"javascript:__doPostBack(&#39;ctl00$MainContent$pagListTop$ctl04$ctl00&#39;,&#39;&#39;)\">Next</a>"
           if next_link else "")
    return (f'<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="vs{start}" />'
            f'<span>Records&nbsp; {start} &nbsp;-&nbsp; {start + len(ids) - 1} &nbsp;of&nbsp; {total} </span>'
            f"{nxt}{rows}")


def test_an_aspnet_job_search_is_paged_by_posting_back_its_next_link():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, text=_aspx_page(range(100), 1, 150))
        return httpx.Response(200, text=_aspx_page(range(100, 150), 101, 150, next_link=False))

    result = _scan(_config({"name": "Northern Health", "platform": "aspnet_jobsearch",
                            "url": "https://jobs.northernhealth.ca", "location_hint": "BC"}), handler)
    assert requests[0].url.path == "/JobSearch/s-/0-0-0-100-0-false-0-0"
    form = requests[1].content.decode()
    assert "__VIEWSTATE=vs1" in form and "__EVENTTARGET=ctl00%24MainContent%24pagListTop%24ctl04%24ctl00" in form
    assert len(requests) == 2 and len(result.postings) == 150
    first = result.postings[0]
    assert first.url == "https://jobs.northernhealth.ca/ViewJobPosting/0"
    assert first.title == "Data Analyst 0" and first.location == "Prince George, BC"
    assert result.health.advertised == 150 and not result.health.incomplete


def test_an_aspnet_page_without_its_record_count_is_an_error():
    result = _scan(_config({"name": "Northern Health", "platform": "aspnet_jobsearch",
                            "url": "https://jobs.northernhealth.ca"}),
                   lambda request: httpx.Response(200, text="<html>maintenance</html>"))
    assert result.health.errors == 1


# Trimmed from careers.albertahealthservices.ca, 2026-09-27.
def _sm_page(ids, pages=2):
    rows = "".join(
        f'<div id="job_list_{i}" class="job_list_row"><div class="jlr_title">'
        f'<p><a href="https://careers.albertahealthservices.ca/jobs/data-analyst-{i}" class="job_link font_bold">'
        f'Data Analyst {i}</a></p><p class="jlr_cat_loc"><span class="location"> North Zone, Valleyview </span></p>'
        f"</div></div>" for i in ids)
    return f'{rows}<div id="jPaginateNumPages" class="ghost">{pages}.0</div>'


def test_a_selectminds_search_is_walked_to_its_published_page_count():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.url.path == "/jobs/search":
            return httpx.Response(302, headers={"Location": "https://careers.albertahealthservices.ca/jobs/search/147"})
        if request.url.path.endswith("/page2"):
            return httpx.Response(200, text=_sm_page(range(10, 13)))
        return httpx.Response(200, text=_sm_page(range(10)))

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    result = scan_employer_boards_source(
        _config({"name": "Alberta Health Services", "platform": "selectminds",
                 "url": "https://careers.albertahealthservices.ca", "location_hint": "Alberta"}),
        client=client, sleep=lambda _s: None)
    assert urls[-1] == "https://careers.albertahealthservices.ca/jobs/search/147/page2"
    assert len(result.postings) == 13 and result.health.ok and not result.health.incomplete
    assert result.postings[0].location == "North Zone, Valleyview, Alberta"


def test_a_selectminds_walk_that_stops_before_its_last_page_is_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/page2"):
            return httpx.Response(200, text=_sm_page([], pages=5))
        return httpx.Response(200, text=_sm_page(range(10), pages=5))

    result = _scan(_config({"name": "AHS", "platform": "selectminds",
                            "url": "https://careers.albertahealthservices.ca"}), handler)
    assert result.health.incomplete


def test_the_tier_keeps_the_positive_title_filter():
    """J.D. Irving's board is drivers, mechanics and store managers; only the
    title says which of its 736 postings is his work."""
    from job_hunt.services.scan import ScanResult, ScannedJob, _accept_jobs

    jobs = [
        ScannedJob(url="https://x/1", title="Business Systems Analyst", company="J.D. Irving",
                   location="Saint John, NB, Canada", portal="oracle", source="employer_boards"),
        ScannedJob(url="https://x/2", title="Class 5 P&D Courier Driver", company="J.D. Irving",
                   location="Sydney, NS, Canada", portal="oracle", source="employer_boards"),
    ]
    result = ScanResult()
    _accept_jobs(jobs, result, positives=["analyst"], negatives=[], include_non_canada=False,
                 known_urls=set(), known_company_roles=set(), apply=False, count_fetched=True)
    assert [job.url for job in result.jobs] == ["https://x/1"]
