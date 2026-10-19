"""The shortlist is chosen, not just sorted — see services/balance.py."""

from __future__ import annotations

import pytest

from job_hunt.services.balance import MIN_SCORE, Mix, balanced_slate
from job_hunt.services.triage import region, remote_region, sector, startup_source


def _pick(items, limit, inbox=None):
    # Items are (company, location, score[, role[, source]]); a missing role
    # stands for a technical one, a missing source for a non-start-up board.
    return balanced_slate(
        items, limit,
        company=lambda item: item[0], location=lambda item: item[1], score=lambda item: item[2],
        role=lambda item: item[3] if len(item) > 3 else "software developer",
        source=lambda item: item[4] if len(item) > 4 else "adzuna",
        inbox=inbox,
    )


@pytest.mark.parametrize(
    "location, expected",
    [
        ("Toronto, Ontario", "gta"),
        ("Markham, Ontario, Canada", "gta"),
        # GTA towns the first version called "outside the GTA" (Codex review 2026-09-16).
        ("Whitby, ON", "gta"),
        ("Burlington, Ontario", "gta"),
        ("Milton, ON", "gta"),
        ("Aurora, ON", "gta"),
        ("Newmarket, ON", "gta"),
        ("Greater Sudbury (ON)", "north"),
        ("Whitehorse, Yukon", "north"),
        ("Yellowknife, NT", "north"),
        ("St. Catharines, Niagara", "ontario"),
        ("London (ON)", "ontario"),
        ("Saskatoon, Saskatchewan", "other_province"),
        ("Brandon MB", "other_province"),
        ("Vancouver, BC (on-site)", "other_province"),  # "on-site" is not Ontario
        ("St. John's, Newfoundland and Labrador", "other_province"),  # not the North
        ("Remote, Canada", "remote"),
        ("WFH, Toronto", "remote"),
        # No positive evidence of a Canadian place: fills no reservation.
        ("Canada", "unknown"),
        ("Unknown", "unknown"),
        ("Upto $85/hr", "unknown"),
        ("Work on site, Canada", "unknown"),
        ("San Francisco, CA, United States", "unknown"),
        ("", "unknown"),
        # Round 2 (Codex review 2026-09-16).
        ("King, ON", "gta"),
        ("Scugog, ON", "gta"),
        ("Brock, ON", "gta"),
        ("Georgetown, PE", "other_province"),  # a province code outranks a city name
        ("Windsor, NS", "other_province"),
        ("Vancouver, WA, United States", "unknown"),
        ("Fort Nelson BC", "north"),  # space-delimited province code
        ("Mississauga (ON)", "gta"),
        ("Victory Square, Vancouver", "other_province"),
        ("Mcquade, Moncton", "other_province"),
        ("Delta, Greater Vancouver", "other_province"),
        ("Canada - Ontario - Toronto", "gta"),
        ("Hybrid - Calgary, AB", "other_province"),
        # Round 3 (Codex review 2026-09-16).
        ("HALIFAX, NS, CA, B3J 0G1", "other_province"),  # CA is the country, not California
        ("Winnipeg, MB, CA", "other_province"),
        ("Toronto, ON, CA", "gta"),
        ("San Jose, CA", "unknown"),
        ("Richmond, BC (on-site)", "other_province"),
        ("Richmond, BC V6X 1A1", "other_province"),
        ("Sydney, NS (Hybrid)", "other_province"),
        ("Truro, NS B2N 5E3", "other_province"),
        ("100 King Street West, Hamilton, ON", "ontario"),  # a street, not King Township
        ("Georgetown, Prince Edward Island", "other_province"),
        ("Georgetown, ON", "gta"),
        # Round 4 (Codex review 2026-09-16, partial run).
        ("King ON", "gta"),
        ("Georgetown ON", "gta"),
        ("Brock University, St. Catharines, ON", "ontario"),
        ("Ontario, California, United States", "unknown"),
        ("New Brunswick, NJ", "unknown"),
        ("Quebec City, QC", "other_province"),
        ("Halifax, Nova Scotia, CA, B3K 4N1", "other_province"),  # real inbox rows
        ("Boston, Massachusetts, United States; Ottawa, Ontario, Canada", "ontario"),
        ("New Brunswick, NJ 08901", "unknown"),
        # agy review 2026-09-16.
        ("Halifax, Nova Scotia, CA", "other_province"),
        ("Toronto, Ontario, CA", "gta"),
        ("King, Hamilton, ON", "ontario"),
        ("Brock, St. Catharines, ON", "ontario"),
        ("Georgetown, DC", "unknown"),
        ("Georgetown, Washington, DC", "unknown"),
        ("Ontario, CA", "ontario"),  # country code on Canadian boards
        ("Ontario, CA 91764", "unknown"),  # a ZIP makes it California
        ("Bolton (ON)", "gta"),
        ("Georgetown (ON)", "gta"),
    ],
)
def test_region_names_a_place_only_on_positive_evidence(location, expected):
    assert region(location) == expected


@pytest.mark.parametrize(
    "location, expected",
    [
        ("Whitehorse, Yukon", True),
        ("Greater Sudbury (ON)", True),
        ("Pictou, NS", True),  # RCIP community
        ("New Glasgow, Nova Scotia", True),
        ("Brandon MB", True),
        ("Moose Jaw, SK", True),
        ("Kirkland Lake, ON", True),
        ("Halifax, NS", False),  # a provincial capital is not remote
        ("St. John's, Newfoundland and Labrador", False),  # the province's name, not Labrador
        ("Bathurst Street, Barrie, ON", False),  # a street named after a town
        ("Brandon Ave, Winnipeg, MB", False),
        ("Bathurst, NB", True),
        ("Val-d’Or, Quebec", True),  # curly apostrophe
        ("Mackenzie, BC", True),  # Canfor mill town
        ("Houston, BC", True),
        ("Houston, TX", False),
        ("Toronto, Ontario", False),
        ("Remote, Canada", False),  # remote work is not a remote region
        ("Vernon, CT 06066", False),  # a US address never counts
        ("Upto $85/hr", False),
    ],
)
def test_remote_region_is_the_north_or_a_named_small_community(location, expected):
    assert remote_region(location) is expected


def test_startup_sources_are_the_early_stage_boards_only():
    assert startup_source("getro")
    assert startup_source("hn_who_is_hiring")
    assert startup_source("ycombinator")
    assert not startup_source("ashby")
    assert not startup_source("adzuna")
    assert not startup_source("")


def test_sector_follows_the_triage_government_vocabulary():
    assert sector("Government of Yukon") == "public"
    assert sector("University of Waterloo") == "public"
    assert sector("OLG") == "public"
    assert sector("Royal Bank of Canada") == "private"
    assert sector("Royal  Bank of Canada") == "private"
    assert sector("National Bank of Canada") == "private"
    assert sector("Bank of Canada") == "public"
    assert sector("Bank of Canada / Banque du Canada") == "public"
    assert sector("Banque du Canada") == "public"
    assert sector("Banque du Canada / Bank of Canada") == "public"
    assert sector("Hydro One") == "public"
    assert sector("Magical") == "private"


@pytest.mark.parametrize(
    "company",
    [
        # Private institutions in the 2026-09-17 inbox that "university|college" called public.
        "CICCC - Cornerstone International Community College of Canada-",
        "International Business University",
        "Yorkville University",
        "Northeastern University",
        "Futures Canadian College of Business Health & Technology",
        "Robertson College",
        "Canadian College of Health Leaders - Northwestern Ontario Chapter",
    ],
)
def test_private_colleges_and_universities_are_private(company):
    assert sector(company) == "private"


@pytest.mark.parametrize(
    "company",
    ["Algonquin College", "Nova Scotia Community College (NSCC)", "Keyano College", "Capilano University",
     "Womens College Hospital"],
)
def test_public_colleges_and_universities_stay_public(company):
    assert sector(company) == "public"


def test_the_north_is_a_lottery_any_technical_role_fills_it_regardless_of_score():
    """Operator 2026-09-17: a full match in the North almost never exists, so
    the North slots go to roles he can reach technically, however low they
    score — and not to a policy or accounting analyst that scores higher."""
    toronto = [(f"Startup{i}", "Toronto, Ontario", 4.0) for i in range(10)]
    # Enough public, outside-GTA rows above them that only the North slots are in question.
    halifax = [("Province of Nova Scotia", "Halifax, NS", 4.0, f"systems analyst {i}") for i in range(10)]
    policy = ("Government of the Northwest Territories", "Yellowknife", 3.0, "Policy Analyst")
    accounting = ("Government of the Northwest Territories", "Yellowknife", 3.0,
                  "Financial Reporting and Accounting Analyst")
    junior = ("Magna Mining Inc.", "Greater Sudbury (ON)", 1.5, "software developer")
    it_analyst = ("Town of Hay River", "Hay River (NT)", -1.0, "information technology (IT) analyst")
    ranked = toronto + halifax + [policy, accounting, junior, it_analyst]

    slate, gaps = _pick(ranked, 20)

    assert junior in slate and it_analyst in slate
    assert policy not in slate and accounting not in slate
    remote = next(g for g in gaps if g.reservation == "remote region")
    assert remote.got == 2 and remote.in_inbox == 2


def test_a_corporate_heavy_ranking_still_yields_remote_region_and_startup_rows():
    """Operator 2026-10-19: mostly remote-region employers and start-ups. A
    long plateau of Toronto corporate rows ranks above both, and the slate
    still gives each lane four of ten slots."""
    toronto = [(f"Corp{i}", "Toronto, Ontario", 3.5) for i in range(40)]
    remote = [
        ("Government of Yukon", "Whitehorse, Yukon", 2.0),
        ("Town of Pictou", "Pictou, NS", 1.0, "IT technician"),
        ("Protocase", "Sydney, NS, Cape Breton", 3.0),
    ]
    startups = [(f"Seed{i}", "Remote, Canada", 2.5, "AI engineer", "getro") for i in range(5)]
    ranked = toronto + startups + remote

    slate, gaps = _pick(ranked, 10)

    assert len(slate) == 10
    assert all(row in slate for row in remote)
    assert sum(1 for row in slate if row in startups) == 4
    assert slate == [item for item in ranked if item in slate]  # ranking order kept
    assert [(g.reservation, g.wanted, g.got, g.in_inbox) for g in gaps] == [("remote region", 4, 3, 3)]
    assert gaps[0].sourcing


def test_rows_below_the_floor_never_fill_a_reservation():
    assert MIN_SCORE == 2.0  # the operator's floor, 2026-09-16
    weak = ("Seed", "Toronto, Ontario", 1.0, "engineer ii", "getro")  # a generic "Engineer II"
    ranked = [(f"Corp{i}", "Toronto, Ontario", 3.5) for i in range(12)] + [weak]
    slate, gaps = _pick(ranked, 5)
    assert weak not in slate
    assert {g.reservation for g in gaps} == {"remote region", "start-up"}


def test_a_gap_says_whether_the_inbox_had_the_rows():
    """Rows the screen or a dead link removed are not a sourcing gap."""
    remote = [("Government of Yukon", "Whitehorse, Yukon", 3.0, f"systems analyst {i}") for i in range(4)]
    survivors = [(f"Startup{i}", "Toronto, Ontario", 3.5) for i in range(10)]
    _slate, gaps = _pick(survivors, 10, inbox=survivors + remote)
    gap = next(g for g in gaps if g.reservation == "remote region")
    assert gap.in_inbox == 4 and gap.got == 0
    assert not gap.sourcing


def test_a_small_slate_keeps_a_slot_for_the_best_row_overall():
    best = ("Corp", "Toronto, Ontario", 5.0)
    ranked = [best] + [("Town of Pictou", "Pictou, NS", 1.0, "IT technician"),
                       ("Seed", "Remote, Canada", 3.0, "AI engineer", "getro")]
    slate, _gaps = _pick(ranked, 3)
    assert best in slate


def test_limit_larger_than_the_pool_returns_every_item_once():
    ranked = [("Government of Yukon", "Whitehorse, Yukon", 3.0), ("Startup", "Toronto, Ontario", 3.0)]
    slate, _gaps = _pick(ranked, 10)
    assert slate == ranked


def test_mix_line_names_every_non_empty_bucket():
    mix = Mix.of([("Government of Yukon", "Whitehorse, Yukon"), ("Magical", "Toronto, Ontario")])
    assert mix.line() == "GTA 1 · North 1 | public 1 / private 1"
