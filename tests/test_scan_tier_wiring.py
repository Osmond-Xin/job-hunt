"""The 2026-09-27 tiers reach scan_portals, each on the right side of the
positive title filter: SaskJobs is NOC-filtered like Job Bank and waives it;
the employer boards, WorkPEI and YC are general boards and keep it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import job_hunt.services.scan as scan_module
from job_hunt.models.posting import JobPosting, SourceHealth, SourceResult


def _source(source_id: str, *titles: str, errors: int = 0):
    def scan(config, *_args, **_kwargs):
        postings = [
            JobPosting(url=f"https://{source_id}.example/{i}", title=title, company="Co",
                       portal=source_id, source=source_id, source_id=source_id,
                       location="Regina, Saskatchewan")
            for i, title in enumerate(titles)
        ]
        return SourceResult(postings=postings, health=SourceHealth(source_id=source_id, ok=not errors, errors=errors))

    return scan


def test_new_tiers_are_scanned_with_the_right_title_filter(tmp_path: Path, monkeypatch) -> None:
    portals = tmp_path / "portals.yml"
    portals.write_text(
        yaml.safe_dump({"title_filter": {"positive": ["analyst"], "negative": []}}), encoding="utf-8"
    )
    monkeypatch.setattr(scan_module, "_known_urls", lambda: set())
    monkeypatch.setattr(scan_module, "_known_company_roles", lambda: set())
    # "Programmer" is not on the positive list: only the NOC-filtered tier keeps it.
    monkeypatch.setattr(scan_module, "scan_saskjobs_source", _source("saskjobs", "Programmer"))
    monkeypatch.setattr(scan_module, "scan_employer_boards_source",
                        _source("employer_boards", "Programmer", "Systems Analyst"))
    monkeypatch.setattr(scan_module, "scan_workpei_source", _source("workpei", "Programmer", errors=2))
    monkeypatch.setattr(scan_module, "scan_yc_companies_source", _source("yc_companies", "Data Analyst"))

    result = scan_module.scan_portals(config_path=portals, mode="full")

    assert sorted((job.portal, job.title) for job in result.jobs) == [
        ("employer_boards", "Systems Analyst"),
        ("saskjobs", "Programmer"),
        ("yc_companies", "Data Analyst"),
    ]
    assert any(w.startswith("workpei: 2 failed request(s)") for w in result.errors)


# The Canada gate over province codes. Round-2 review 2026-09-27; the rule is
# cost-asymmetric: a foreign posting let through costs one read, a Canadian one
# dropped is lost for good, so doubt resolves to accept.
CANADIAN = [
    "Taber, AB", "AB", "Lethbridge County, AB", "Truro, NS, Canada", "Taber, AB, CA",
    "Prince Albert, SK", "Swift Current, SK S9H 0A1", "Summerside, PE", "Summerside, PE, CA",
    "Summerside, PE C1N 1A1", "Corner Brook, NL", "Gander, NL", "Corner Brook, NL, CA",
    "Hay River, NT", "Dawson City, YT", "Rankin Inlet, NU", "Cobourg, ON, K9A 4J9",
    "Truro, NS, B2N 1A1", "Toronto, ON, Canada, Remote", "Vancouver, BC, Canada, Hybrid",
    "Taber, AB T1G 2C9", "London, ON", "St. John's, NL", "Moncton, NB, Canada",
    "Whitehorse, YT", "Regina, SK", "Lethbridge County, AB T1J4L1, Canada",
    "Toronto, CA", "Toronto, ON / New York, NY", "Toronto, ON, North America",
    "Remote US/Canada",
    # Job Bank writes the province in brackets (smoke run 2026-09-27).
    "Burnaby (BC)", "Etobicoke (ON)", "Gatineau (QC)", "Elmsdale (NS)", "Hay River (NT)",
    # Round 3: Canadian evidence is not vetoed by foreign words beside it.
    "New Germany, NS", "New Germany, NS, Canada", "Toronto, ON, Canada and US",
    "Toronto, ON, Canada & US", "Remote (US and Canada), Toronto, ON",
    "Toronto, ON, Boston, MA, United States", "Montreal, QC, Paris, France",
    "Calgary, Alberta, NE", "Toronto, OR, Canada", "Burnaby (BC) - Remote", "Burnaby (BC), Remote",
    "Swift Current (SK) - Hybrid", "Burnaby (BC/Colombie-Britannique)",
    "Caraquet, Nouveau-Brunswick", "Nouvelle-Écosse", "Cobourg, ON k9a 4j9",
    "Bedford / London", "Dartmouth; New York", "Remote (US/CA)", "US/CA", "CA/US", "   ",
    # Accepted on purpose: BC is Canadian evidence and a doubtful row costs one read.
    "Vancouver, BC, United States", "Munich, BC, Germany", "Munich, BC, CA - United States",
    "Mexico City, NL",
    # Round 4: a code is a whole field, never the first word of one.
    "Toronto, IN PERSON", "Vancouver, IN PERSON", "Toronto, IN OFFICE", "Toronto, OR Remote",
    "Calgary, NE", "CA-ON-Whitby", "CA-BC-Burnaby", "CA-AB-Canmore", "CA-QC-Sherbrooke",
    # Explicit Canadian evidence beats a collision city (only Baja overrides BC).
    "Amsterdam, NL, Canada", "Darwin, NT, Toronto, ON",
    # Round 5, allow-listed city with a work-mode note.
    "London, ON (Hybrid)",
]
FOREIGN = [
    "Waterloo, IA", "Waterloo, IA 50701", "Vancouver, WA 98660", "Amsterdam, NL",
    "Amsterdam, NL, Netherlands", "Bratislava, SK", "Lima, PE", "Darwin, NT, Australia",
    "Monterrey, NL, Mexico", "Tijuana, BC, Mexico", "Austin, TX", "Waterloo, IA, United States",
    "Seattle, WA", "Austin, TX, US", "Remote, on-site in Boston", "San Francisco, CA",
    "Lima (PE)", "Amsterdam (NL)",
    # Round 4.
    "San Francisco, CA (ON-SITE)", "Austin, TX (ON-SITE)", "New York, NY (ON-SITE)",
    "London, UK (ON SITE)", "San Francisco, CA, ON SITE", "Austin, TX, ON SITE",
    "(Remote) Amsterdam (NL)", "(Remote) Tijuana (BC)", "Tijuana, Mexico (BC)",
    "Amsterdam (HQ), NL",
]


# Accepted by the CODE path, not by name: none of these cities is on an allow
# list, so `_matches_canada_by_name` alone would drop every one — asserted
# below, so a case that starts passing by name instead is noticed.
CODE_PATH_CANADIAN = [
    "Burnaby, BC", "Burnaby, BC - Remote", "Burnaby, BC (Hybrid)", "Burnaby, BC - On-site",
    "Burnaby, BC – Remote", "Burnaby, BC — Remote", "Kanata, ON - Hybrid", "Kanata, ON (Hybrid)",
    "Vaughan, ON - Hybrid", "Laval, QC - Remote", "Gatineau, QC - Hybrid", "Red Deer, AB - Remote",
    "Taber, AB - On-site", "Taber, AB T1G 2C9 - Hybrid", "Truro, NS - Hybrid",
    "Corner Brook, NL (Remote)", "Corner Brook, NL - Remote", "Swift Current, SK - Hybrid",
    "Swift Current, SK S9H 0A1", "Burnaby (BC) - Remote", "CA-BC-Burnaby",
]
# Rejected by the CODE path: a code with a glued dash or a bare word after it
# is not a code, and US states follow the same rule as provinces.
CODE_PATH_FOREIGN = [
    "Burnaby, BC-SITE", "Kanata, ON SITE", "Kanata, ON Remote",
    "Vancouver, WA (ON-SITE)", "Waterloo, IA (IN OFFICE)", "Waterloo, IA - Hybrid",
    "Waterloo, IA 50701 - Remote",
]


@pytest.mark.parametrize("location", CODE_PATH_CANADIAN)
def test_a_province_code_field_accepts_where_the_name_would_not(location) -> None:
    from job_hunt.services.scan import _matches_canada_by_name, _passes_canada_filter

    assert _matches_canada_by_name(location) is False, "case no longer exercises the code path"
    assert _passes_canada_filter(location) is True


@pytest.mark.parametrize("location", CODE_PATH_FOREIGN)
def test_a_code_with_a_glued_or_bare_word_after_it_is_not_a_province(location) -> None:
    from job_hunt.services.scan import _passes_canada_filter

    assert _passes_canada_filter(location) is False


@pytest.mark.parametrize("location", CANADIAN)
def test_a_canadian_location_passes_the_gate(location) -> None:
    from job_hunt.services.scan import _passes_canada_filter

    assert _passes_canada_filter(location) is True


@pytest.mark.parametrize("location", FOREIGN)
def test_a_foreign_location_is_stopped_at_the_gate(location) -> None:
    from job_hunt.services.scan import _passes_canada_filter

    assert _passes_canada_filter(location) is False


@pytest.mark.parametrize("location", [None, 42, ["Toronto"]])
def test_a_location_that_is_not_text_is_unknown_and_accepted(location) -> None:
    from job_hunt.services.scan import _passes_canada_filter

    assert _passes_canada_filter(location) is True


def test_saskjobs_is_filtered_by_job_banks_noc_codes(tmp_path: Path, monkeypatch) -> None:
    portals = tmp_path / "portals.yml"
    portals.write_text(
        yaml.safe_dump({"jobbank_direct": {"enabled": False, "noc_codes": ["21221"]},
                        "saskjobs": {"enabled": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(scan_module, "_known_urls", lambda: set())
    monkeypatch.setattr(scan_module, "_known_company_roles", lambda: set())
    received: list = []

    def fake(config, noc_codes):
        received.append((config, noc_codes))
        return SourceResult(postings=[], health=SourceHealth(source_id="saskjobs", ok=True))

    monkeypatch.setattr(scan_module, "scan_saskjobs_source", fake)
    scan_module.scan_portals(config_path=portals, mode="full")
    assert received == [({"enabled": True}, ["21221"])]
