"""Tests for the shared date/age normalizer used by both gov_boards.py
(GNWT's "N units ago") and scan.py's Workday / Adzuna / Job Bank mappers.

This module exists because the "relative age string -> ISO date" arithmetic
briefly existed as two separate, drifting copies — one in gov_boards.py, one
in scan.py — before being consolidated here.
"""

from __future__ import annotations

import datetime as dt

from job_hunt.services.posted_date import (
    adzuna_created_to_iso,
    jobbank_date_to_iso,
    relative_age_to_iso,
)

TODAY = dt.date(2026, 8, 13)


def test_relative_age_handles_every_unit_gov_boards_relies_on() -> None:
    """Same fixtures test_gov_boards_nb_mb.py exercises through _age_to_iso."""
    assert relative_age_to_iso("Posted 50 min ago", TODAY) == "2026-08-13"
    assert relative_age_to_iso("Posted 17 hours ago", TODAY) == "2026-08-13"
    assert relative_age_to_iso("Posted 3 days ago", TODAY) == "2026-08-10"
    assert relative_age_to_iso("Posted 2 weeks ago", TODAY) == "2026-07-30"
    # Anything that is not an interval stays empty rather than becoming today.
    assert relative_age_to_iso("", TODAY) == ""
    assert relative_age_to_iso("Posted recently", TODAY) == ""


def test_relative_age_handles_workdays_today_and_yesterday_phrasing() -> None:
    assert relative_age_to_iso("Posted Today", today=TODAY) == "2026-08-13"
    assert relative_age_to_iso("Posted Yesterday", today=TODAY) == "2026-08-12"


def test_relative_age_handles_workdays_days_ago_phrasing() -> None:
    assert relative_age_to_iso("Posted 3 Days Ago", today=TODAY) == "2026-08-10"


def test_relative_age_nudges_the_plus_floor_one_day_older() -> None:
    """"30+ Days Ago" is a floor, not an exact count, so it is nudged one day
    older to keep triage's >30-day staleness cut from missing it.
    """
    assert relative_age_to_iso("Posted 30+ Days Ago", today=TODAY) == "2026-07-13"


def test_adzuna_created_to_iso_trims_the_timestamp() -> None:
    assert adzuna_created_to_iso("2026-08-01T00:00:00Z") == "2026-08-01"
    assert adzuna_created_to_iso("") == ""


def test_jobbank_date_to_iso_parses_the_month_name_date() -> None:
    assert jobbank_date_to_iso("August 06, 2026") == "2026-08-06"
    assert jobbank_date_to_iso("") == ""
    assert jobbank_date_to_iso("garbage") == ""
