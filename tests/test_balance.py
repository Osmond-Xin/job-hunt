"""The shortlist is chosen, not just sorted — see services/balance.py."""

from __future__ import annotations

import pytest

from job_hunt.services.balance import MIN_SCORE, Mix, balanced_slate, outside_gta
from job_hunt.services.triage import region, sector


def _pick(items, limit, inbox=None):
    return balanced_slate(
        items, limit,
        company=lambda item: item[0], location=lambda item: item[1], score=lambda item: item[2],
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
    ],
)
def test_region_names_a_place_only_on_positive_evidence(location, expected):
    assert region(location) == expected


def test_only_named_places_outside_the_gta_count_as_outside_it():
    assert outside_gta("Halifax, NS")
    assert not outside_gta("Whitby, ON")
    assert not outside_gta("Remote, Canada")
    assert not outside_gta("Upto $85/hr")


def test_sector_follows_the_triage_government_vocabulary():
    assert sector("Government of Yukon") == "public"
    assert sector("University of Waterloo") == "public"
    assert sector("OLG") == "public"
    assert sector("Royal Bank of Canada") == "private"
    assert sector("Royal  Bank of Canada") == "private"
    assert sector("National Bank of Canada") == "private"
    assert sector("Bank of Canada") == "public"
    assert sector("Hydro One") == "public"
    assert sector("Magical") == "private"


def test_a_toronto_heavy_ranking_still_yields_north_public_and_outside_gta_rows():
    """The 2026-09-16 shape: a long plateau of Toronto private rows above a
    Yukon government analyst that scores lower but is still a match."""
    toronto = [(f"Startup{i}", "Toronto, Ontario", 3.5) for i in range(40)]
    yukon = ("Government of Yukon", "Whitehorse, Yukon", 2.0)
    brock = ("Brock University", "St. Catharines, Ontario", 3.5)
    halifax = [(f"NS Co{i}", "Halifax, Nova Scotia", 2.5) for i in range(5)]
    ranked = toronto + [brock] + halifax + [yukon]

    slate, gaps = _pick(ranked, 10)

    assert len(slate) == 10
    assert yukon in slate
    assert brock in slate
    assert sum(1 for _c, loc, _s in slate if outside_gta(loc)) >= 5
    assert slate == [item for item in ranked if item in slate]  # ranking order kept
    assert [(g.reservation, g.wanted, g.got, g.in_inbox) for g in gaps] == [("public sector", 3, 2, 2)]
    assert gaps[0].sourcing


def test_rows_below_the_floor_never_fill_a_reservation():
    assert MIN_SCORE == 2.0  # the operator's floor, 2026-09-16
    ranked = [(f"Startup{i}", "Toronto, Ontario", 3.5) for i in range(12)] + [
        ("City of Windsor", "Windsor, Ontario", 1.0),  # a generic "Engineer II"
    ]
    slate, gaps = _pick(ranked, 5)
    assert ("City of Windsor", "Windsor, Ontario", 1.0) not in slate
    assert {g.reservation for g in gaps} == {"North", "public sector", "outside GTA"}


def test_a_gap_says_whether_the_inbox_had_the_rows():
    """Rows the screen or a dead link removed are not a sourcing gap."""
    yukon = ("Government of Yukon", "Whitehorse, Yukon", 3.0)
    survivors = [(f"Startup{i}", "Toronto, Ontario", 3.5) for i in range(10)]
    _slate, gaps = _pick(survivors, 10, inbox=survivors + [yukon])
    north = next(g for g in gaps if g.reservation == "North")
    assert north.in_inbox == 1 and north.got == 0
    assert not north.sourcing


def test_limit_larger_than_the_pool_returns_every_item_once():
    ranked = [("Government of Yukon", "Whitehorse, Yukon", 3.0), ("Startup", "Toronto, Ontario", 3.0)]
    slate, _gaps = _pick(ranked, 10)
    assert slate == ranked


def test_mix_line_names_every_non_empty_bucket():
    mix = Mix.of([("Government of Yukon", "Whitehorse, Yukon"), ("Magical", "Toronto, Ontario")])
    assert mix.line() == "GTA 1 · North 1 | public 1 / private 1"
