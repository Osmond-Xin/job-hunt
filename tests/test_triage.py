"""Pipeline triage: turning an unreadable inbox into a day's shortlist.

The ordering encodes standing decisions, so these tests are mostly about those
decisions holding — role shape deciding rank on its own now that geography
scores and orders nothing (removed 2026-09-03: the operator looks for a
matching job first and immigration second, and immigration reaches him
downstream in `services/immigration.py` instead), and already-applied work
never resurfacing.
"""

from __future__ import annotations

from datetime import date

from job_hunt.services.triage import (
    PipelineRow,
    already_applied,
    applied_employer,
    excluded,
    parse_pipeline,
    rank,
    score,
    submitted_employers,
    tracker_seen,
)

PIPELINE = """# Pipeline

## Pending

- [ ] https://example.invalid/1 | Government of Manitoba | Data Engineer | Winnipeg MB | posted 2026-08-11 | source: mb_gov
- [ ] https://example.invalid/2 | BigCo | Senior AI Engineer | Toronto, Ontario | posted 2026-08-11 | source: adzuna
- [ ] https://example.invalid/3 | Randstad | AI Engineer | Halifax, NS | posted 2026-08-11 | source: adzuna
- [ ] https://example.invalid/4 | RealCo | Director of Engineering | Halifax, NS | posted 2026-08-11 | source: adzuna
- [x] https://example.invalid/5 | Done | AI Engineer | Halifax, NS | posted 2026-08-01 | source: adzuna
"""

TRACKER = """| # | Date | Company | Role | Score | Status | PDF | Notes |
| 723 | 2026-08-12 | Government of New Brunswick — Finance and Treasury Board, OCIO | AI Integration and Automation Specialist (Competition 16958) | 4.0/5 | Applied | y | url=https://example.invalid/applied |
| 999 | 2026-08-12 |  |  | | Evaluated | y | blank cells |
"""


def test_parses_only_unchecked_rows():
    rows = parse_pipeline(PIPELINE)
    assert [row.company for row in rows] == [
        "Government of Manitoba",
        "BigCo",
        "Randstad",
        "RealCo",
    ]
    assert rows[0].posted == "2026-08-11"
    assert rows[0].source == "mb_gov"


def test_a_url_already_settled_does_not_come_back_under_a_new_company_name():
    """The real case: two NS geomatics postings closed in April.

    Discovery re-added them from a websearch source that called the employer
    "NS Public Service" rather than "NS Geomatics Centre", so neither the URL
    nor the company+role dedupe matched, and they rode back into the shortlist
    twenty rows deep and four months late.
    """
    text = PIPELINE + (
        "- [!] https://example.invalid/6 | NS Geomatics Centre | Systems Analyst"
        " | Amherst, NS | closed — verified 2026-04-13\n"
        "- [ ] https://example.invalid/6 | NS Public Service | Systems Analyst Job Details"
        " | source: websearch\n"
    )
    assert "https://example.invalid/6" not in {row.url for row in parse_pipeline(text)}


def test_location_no_longer_affects_the_score_at_all():
    """Removed entirely 2026-09-03 on the operator's ruling.

    This test used to assert region as a bounded tie-break (a correction of
    an earlier version where region dominated outright: a Brandon equipment
    operator scored 7.5 against 4.0 for Mistral's Applied AI forward-deployed
    role, and 130 of the top 300 rows were government postings). Bounding it
    still wasn't the fix — the tiers were built for an immigration-path
    ranking the operator's own later research reversed. His decision: a job
    match first, immigration second — "两个能一起解决最好，但是不一起肯定先
    找工作" — so a Toronto row and a Halifax row with otherwise identical
    content must score identically, full stop.
    """
    toronto = PipelineRow(
        url="https://example.invalid/toronto", company="Foundry", role="AI Engineer",
        location="Toronto, Ontario", posted="2026-08-11", source="direct",
    )
    halifax = PipelineRow(
        url="https://example.invalid/halifax", company="Foundry", role="AI Engineer",
        location="Halifax, NS", posted="2026-08-11", source="direct",
    )
    today = date(2026, 8, 12)
    assert score(toronto, today=today) == score(halifax, today=today)


def test_rank_order_does_not_depend_on_location():
    """Same property at the rank() level: ties are broken by freshness then
    company name, never by geography.

    Under the tier scheme this replaced, a nomination-qualifying region beat
    a major metro in the tie-break regardless of company name — so a
    Halifax "Zeta" would have outranked a Toronto "Alpha" even though "Alpha"
    sorts first alphabetically. It must not now: with scores tied, company
    name alone decides, so "Alpha" (Toronto) comes first despite Toronto
    being listed second in the input.
    """
    toronto = PipelineRow(
        url="https://example.invalid/alpha", company="Alpha", role="AI Engineer",
        location="Toronto, Ontario", posted="2026-08-11", source="direct",
    )
    halifax = PipelineRow(
        url="https://example.invalid/zeta", company="Zeta", role="AI Engineer",
        location="Halifax, NS", posted="2026-08-11", source="direct",
    )
    today = date(2026, 8, 12)
    ranked = rank([halifax, toronto], limit=10, today=today)
    assert [item.row.company for item in ranked] == ["Alpha", "Zeta"]


def test_a_role_matching_no_target_vocabulary_sinks_below_a_matching_one():
    """An equipment operator in a PNP province must not outrank an AI role."""
    operator = PipelineRow(
        url="https://example.invalid/op", company="Government of Manitoba",
        role="Equipment Operator", location="Brandon MB",
        posted="2026-08-11", source="mb_gov",
    )
    toronto = next(r for r in parse_pipeline(PIPELINE) if r.company == "BigCo")
    today = date(2026, 8, 12)
    points, reasons = score(operator, today=today)
    assert "off-target role" in reasons
    assert points < score(toronto, today=today)[0]


def test_toronto_is_not_excluded_or_scored_on_location():
    """Toronto used to be down-ranked, then merely a tie-break loser; now
    location plays no part in the score at all."""
    rows = parse_pipeline(PIPELINE)
    toronto = next(r for r in rows if r.company == "BigCo")
    assert excluded(toronto) == ""
    points, reasons = score(toronto, today=date(2026, 8, 12))
    assert "major metro" not in reasons
    assert "PNP/AIP region" not in reasons
    assert points > 0


def test_the_categories_that_converted_at_zero_are_dropped():
    rows = parse_pipeline(PIPELINE)
    assert excluded(next(r for r in rows if r.company == "Randstad")) == "staffing agency"
    assert excluded(next(r for r in rows if r.role.startswith("Director"))) == "above reachable level"


def test_stale_postings_lose_a_point():
    rows = parse_pipeline(PIPELINE)
    row = rows[0]
    fresh, _ = score(row, today=date(2026, 8, 12))
    stale, reasons = score(row, today=date(2026, 10, 1))
    assert stale < fresh
    assert "stale" in reasons


def test_applied_work_does_not_resurface_despite_different_wording():
    _urls, pairs = tracker_seen(TRACKER)
    rows = parse_pipeline(
        "- [ ] https://example.invalid/9 | Government of New Brunswick | "
        "AI Integration and Automation Specialist | NB, Canada | source: nb_gov\n"
    )
    # Neither the company nor the role string matches the tracker exactly.
    assert already_applied(rows[0], pairs) is True


def test_a_blank_tracker_row_does_not_match_everything():
    # An empty cell normalises to "", and startswith("") is always true — which
    # silently emptied the entire shortlist the first time this ran.
    _urls, pairs = tracker_seen(TRACKER)
    rows = parse_pipeline(PIPELINE)
    assert not already_applied(rows[0], pairs)
    assert rank(rows, limit=10, seen_pairs=pairs)


def test_other_roles_at_an_applied_employer_still_surface():
    # One GNB competition is not a reason to hide the rest of the civil service.
    _urls, pairs = tracker_seen(TRACKER)
    rows = parse_pipeline(
        "- [ ] https://example.invalid/8 | Government of New Brunswick | "
        "Quality Assurance Analyst | NB, Canada | source: nb_gov\n"
    )
    assert already_applied(rows[0], pairs) is False


def test_the_same_job_reposted_under_many_locations_appears_once():
    repeated = "".join(
        f"- [ ] https://example.invalid/dup{i} | Smallco | Software Engineer II | "
        f"Halifax, Nova Scotia, Canada | source: linkedin\n"
        for i in range(40)
    )
    ranked = rank(parse_pipeline(repeated), limit=10)
    assert len(ranked) == 1


def test_urls_already_in_the_tracker_are_skipped():
    urls, _pairs = tracker_seen(TRACKER)
    rows = parse_pipeline(
        "- [ ] https://example.invalid/applied | Someone | AI Engineer | Halifax, NS | source: x\n"
    )
    assert rank(rows, limit=5, seen_urls=urls) == []


def test_applied_ai_outranks_generic_full_stack():
    """Role shape is a tier list, not a tally.

    The three vocabularies used to accumulate, so "Full Stack Engineer" took
    one-person scope (+2) *and* adjacent (+1) while "Forward Deployed Engineer"
    took applied AI (+3) alone — and lost the tie, because the FDE rows arrive
    through aggregators and forfeit the direct-source half point. On 2026-09-04
    that put twelve generic full-stack rows on the shortlist and left 631
    applied-AI and forward-deployed rows below the cut.
    """
    fde = PipelineRow(
        url="https://example.invalid/fde", company="Startup", role="Forward Deployed Engineer",
        location="Toronto, Ontario", posted="", source="adzuna",
    )
    full_stack = PipelineRow(
        url="https://example.invalid/fs", company="Startup", role="Full Stack Engineer",
        location="Toronto, Ontario", posted="", source="ashby",
    )
    assert score(fde)[0] > score(full_stack)[0]


def test_the_freshest_posting_wins_a_tie_and_an_undated_one_loses_it():
    """Ties sorted on the raw date string, ascending, until 2026-09-04 — so the
    oldest posting won, and an undated row ("" sorts before any date) won
    outright. Most rows are undated, which left company name alphabetically as
    the real tie-break and returned nothing from Calgary out of a pool of 200
    while 290 Calgary rows waited in the inbox."""
    rows = parse_pipeline(
        "- [ ] https://example.invalid/old | Zeta | AI Engineer | Calgary AB | posted 2026-08-01 | source: adzuna\n"
        "- [ ] https://example.invalid/new | Yankee | AI Engineer | Calgary AB | posted 2026-08-30 | source: adzuna\n"
        "- [ ] https://example.invalid/none | Alpha | AI Engineer | Calgary AB | source: adzuna\n"
    )
    ranked = rank(rows, limit=10, today=date(2026, 8, 31))
    assert [item.row.company for item in ranked] == ["Yankee", "Zeta", "Alpha"]


EMPLOYER_TRACKER = """| # | Date | Company | Role | Score | Status | PDF | Notes |
| 737 | 2026-08-15 | Procurify | Engineering Manager, AI Engineering | 3.6/5 | Applied | y | sent |
| 865 | 2026-09-01 | Citigroup | Agentic AI Solutions Architect | 2.6/5 | Evaluated | y | skip |
| 723 | 2026-08-12 | Government of New Brunswick — OCIO | AI Integration Specialist | 4.0/5 | Applied | y | sent |
"""


def test_a_second_role_at_an_employer_already_applied_to_is_hidden():
    """One role per employer. The pair rule cannot see this: a different title
    at the same company matches nothing, which is how Procurify, Irving Oil and
    Signal 1 all came back on 2026-09-04 after being applied to."""
    employers = submitted_employers(EMPLOYER_TRACKER)
    row = parse_pipeline(
        "- [ ] https://example.invalid/p | Procurify | Senior Full Stack Engineer | "
        "Canada Remote | source: ashby\n"
    )[0]
    assert applied_employer(row, employers) == "737"
    assert rank([row], limit=5, seen_employers=employers) == []


def test_an_employer_only_evaluated_is_not_hidden():
    """Materials built and a decision not to send is not an application."""
    employers = submitted_employers(EMPLOYER_TRACKER)
    row = parse_pipeline(
        "- [ ] https://example.invalid/c | Citigroup | Full Stack Developer | "
        "Mississauga | source: adzuna\n"
    )[0]
    assert applied_employer(row, employers) == ""


def test_the_employer_rule_does_not_hide_a_government():
    """A public competition is scored against stated qualifications, so one GNB
    competition is not a reason to hide the rest of the civil service."""
    employers = submitted_employers(EMPLOYER_TRACKER)
    row = parse_pipeline(
        "- [ ] https://example.invalid/g | Government of New Brunswick | "
        "Quality Assurance Analyst | NB | source: nb_gov\n"
    )[0]
    assert applied_employer(row, employers) == ""


def test_a_row_whose_employer_is_a_location_is_dropped():
    """The company field holds an aggregator's page title, so the employer is
    nowhere in the row. Two of these ranked in the top ten on 2026-09-04 and
    neither could be evaluated: Indeed answers 401, and no résumé can be
    tailored to a postal code."""
    rows = parse_pipeline(
        "- [ ] https://ca.indeed.com/viewjob?jk=1 | Greater Sudbury, ON P3A 5N8 - Indeed.com | "
        "business systems analyst | Sudbury | source: indeed_smalltown\n"
        "- [ ] https://ca.indeed.com/viewjob?jk=2 | Halifax, NS - Job posting - Job Bank | "
        "Data Analyst | Halifax | source: jobbank\n"
        "- [ ] https://x.invalid/3 | Feathery | Full Stack Engineer | Toronto | source: ashby\n"
    )
    assert excluded(rows[0]) == "employer not identified"
    assert excluded(rows[1]) == "employer not identified"
    assert excluded(rows[2]) == ""


def test_the_public_sector_point_does_not_rescue_an_off_target_role():
    """A preference between comparable jobs, not a rescue.

    Paid flat, it let a service-desk analyst and a bilingual French language
    services officer tie with every applied-AI role in the inbox on 2026-09-04.
    """
    service_desk = PipelineRow(
        url="https://example.invalid/sd", company="Government of New Brunswick",
        role="Service Desk Analyst", location="NB", posted="2026-09-01", source="nb_gov",
    )
    gov_ai = PipelineRow(
        url="https://example.invalid/ga", company="Government of New Brunswick",
        role="AI Integration Specialist", location="NB", posted="2026-09-01", source="nb_gov",
    )
    today = date(2026, 9, 4)
    assert "public sector" not in score(service_desk, today=today)[1]
    assert "public sector" in score(gov_ai, today=today)[1]
    assert score(gov_ai, today=today)[0] > score(service_desk, today=today)[0]


def test_the_direct_employer_bonus_reads_the_url_not_the_source_label():
    """"websearch" is not a source, it is a way of finding one.

    It collected the direct-employer half point on 2026-09-04 for rows whose
    URL was linkedin.com — a host this system refuses to fetch — so those
    outranked postings on the employer's own ATS, which are the only ones that
    can be read and applied to.
    """
    rows = parse_pipeline(
        "- [ ] https://ca.linkedin.com/jobs/view/ml-engineer-at-kinaxis-1 | Kinaxis | "
        "Applied Scientist AI | Halifax | source: websearch\n"
        "- [ ] https://job-boards.greenhouse.io/warp/jobs/1 | Warp | "
        "Forward Deployed Engineer | Remote | source: websearch\n"
    )
    assert score(rows[1])[0] > score(rows[0])[0]


def test_a_listing_page_and_a_french_role_are_dropped():
    rows = parse_pipeline(
        "- [ ] https://wellfound.com/role/l/ml/canada | Startups | "
        "Machine Learning Engineer Jobs in Canada | Canada | source: websearch\n"
        "- [ ] https://x.invalid/fr | Government of Manitoba | "
        "Bilingual French Language Services Analyst | Winnipeg MB | source: mb_gov\n"
    )
    assert excluded(rows[0]) == "a listing page, not one posting"
    assert excluded(rows[1]) == "French required"


def test_large_employers_are_dropped_but_governments_are_not():
    # Operator's call: a bank screens on credentials he does not have and has a
    # full funnel of similar candidates. A public competition is scored against
    # stated qualifications, so government stays in even when it is enormous.
    rows = parse_pipeline(
        "- [ ] https://x.invalid/1 | RBC | Fullstack AI Engineer | Halifax, NS | source: linkedin\n"
        "- [ ] https://x.invalid/2 | Government of Nova Scotia | Data Analyst | Halifax, NS | source: ns_gov\n"
        "- [ ] https://x.invalid/3 | Tiny Halifax Startup | AI Engineer | Halifax, NS | source: dns\n"
    )
    assert excluded(rows[0]) == "large employer"
    assert excluded(rows[1]) == ""
    assert excluded(rows[2]) == ""
