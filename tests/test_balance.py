"""The shortlist is chosen, not just sorted — see services/balance.py."""

from __future__ import annotations

from job_hunt.services.balance import Mix, Quotas, balanced_slate, region, sector


def _pick(items, limit, quotas=Quotas()):
    return balanced_slate(
        items, limit,
        company=lambda item: item[0], location=lambda item: item[1], score=lambda item: item[2],
        quotas=quotas,
    )


def test_regions_are_read_from_the_free_text_boards_actually_emit():
    assert region("Toronto, Ontario") == "gta"
    assert region("列治文山, Greater Toronto Area, ON, Canada") == "gta"
    assert region("Greater Sudbury (ON)") == "north"
    assert region("Whitehorse, Yukon") == "north"
    assert region("Yellowknife, NT") == "north"
    assert region("St. Catharines, Niagara, Ontario") == "ontario"
    assert region("Saskatoon, Saskatchewan") == "other_province"
    assert region("Remote, Canada") == "remote"
    assert region("Canada") == "unknown"
    assert region("") == "unknown"


def test_sector_follows_the_triage_government_vocabulary():
    assert sector("Government of Yukon") == "public"
    assert sector("University of Waterloo") == "public"
    assert sector("Magical") == "private"


def test_a_toronto_heavy_ranking_still_yields_north_public_and_outside_gta_rows():
    """The 2026-09-16 shape: a long plateau of Toronto private rows above a
    Yukon government analyst that scores lower but is still a match."""
    toronto = [(f"Startup{i}", "Toronto, Ontario", 3.5) for i in range(40)]
    yukon = ("Government of Yukon", "Whitehorse, Yukon", 2.0)
    brock = ("Brock University", "St. Catharines, Ontario", 3.5)
    halifax = [(f"NS Co{i}", "Halifax, Nova Scotia", 2.5) for i in range(5)]
    ranked = toronto + [brock] + halifax + [yukon]

    slate, shortfalls = _pick(ranked, 10)

    assert len(slate) == 10
    assert yukon in slate
    assert brock in slate
    assert sum(1 for c, loc, _ in slate if region(loc) not in {"gta", "unknown", "remote"}) >= 5
    # Order is still the ranking's order.
    assert slate == [item for item in ranked if item in slate]
    # Only one Yukon + one Brock public row exist, against 3 wanted.
    assert [(s.reservation, s.wanted, s.got) for s in shortfalls] == [("public", 3, 2)]


def test_an_off_target_row_is_never_used_to_fill_a_reservation():
    ranked = [(f"Startup{i}", "Toronto, Ontario", 3.5) for i in range(12)] + [
        ("City of Windsor", "Windsor, Ontario", -1.0),  # off-target role
    ]
    slate, shortfalls = _pick(ranked, 5)
    assert ("City of Windsor", "Windsor, Ontario", -1.0) not in slate
    assert {s.reservation for s in shortfalls} == {"North", "public", "outside GTA"}


def test_without_reservations_the_slate_is_the_plain_top_n():
    ranked = [(f"Co{i}", "Toronto, Ontario", 10 - i) for i in range(10)] + [("City of X", "Regina, SK", 1.0)]
    slate, shortfalls = _pick(ranked, 3, Quotas(public=0, outside_gta=0, north=0))
    assert slate == ranked[:3]
    assert shortfalls == []


def test_mix_line_names_every_non_empty_bucket():
    mix = Mix.of([("Government of Yukon", "Whitehorse, Yukon"), ("Magical", "Toronto, Ontario")])
    assert mix.line() == "GTA 1 · North 1 | public 1 / private 1"
