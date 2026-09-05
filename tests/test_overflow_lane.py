"""Tests for the reserved overflow slots in `job-hunt triage --screen`.

Renamed 2026-09-03 from `test_immigration_lane.py`. The lane under test never
read geography or immigration status — it only ever compared
`Ranked.score`, the same deterministic fit score every row gets — so the old
name was claiming a protection the code did not provide.
"""

from __future__ import annotations

from job_hunt.services.shortlist import _OVERFLOW_LANE_SLOTS, _overflow_lane
from job_hunt.services.triage import PipelineRow, Ranked


def _ranked(company: str, score: float) -> Ranked:
    row = PipelineRow(
        url=f"https://example.invalid/{company.lower()}",
        company=company,
        role="Analyst",
        location="Yellowknife, NT",
        posted="2026-08-16",
        source="gnwt",
    )
    return Ranked(row=row, score=score, reasons=("public sector",))


def test_lane_rescues_the_highest_scoring_rows_the_shortlist_left_out() -> None:
    """The 2026-08-16 case: GNWT rows scored 8.5 and 7.5 and still sank.

    The model rated them fit 3 while a Nova Scotia full-stack post got 4, and
    fit is the primary sort key, so no territorial row could reach the list.
    """
    privacy = _ranked("GNWT-Privacy", 8.5)
    support = _ranked("GNWT-Support", 7.5)
    stale = _ranked("GNWT-Stale", 2.0)
    shortlisted = [_ranked("Foci", 7.5), _ranked("Legend", 5.5)]
    pool = [*shortlisted, privacy, support, stale]

    lane = _overflow_lane(pool, shortlisted)

    assert lane == [privacy, support]
    assert len(lane) == _OVERFLOW_LANE_SLOTS


def test_lane_never_repeats_a_row_already_on_the_shortlist() -> None:
    top = _ranked("Foci", 9.0)
    lane = _overflow_lane([top], [top])
    assert lane == []


def test_lane_is_empty_without_a_screened_pool() -> None:
    """Unscreened triage is already in deterministic-score order — nothing to rescue."""
    assert _overflow_lane([], [_ranked("Foci", 7.5)]) == []


def test_lane_returns_fewer_than_its_slots_when_the_pool_is_short() -> None:
    only = _ranked("GNWT-Privacy", 8.5)
    shortlisted = [_ranked("Foci", 7.5)]
    assert _overflow_lane([*shortlisted, only], shortlisted) == [only]


def test_lane_has_no_geography_or_immigration_logic() -> None:
    """The property the rename records: it is pure score overflow.

    A row in a non-nomination location with a high deterministic score is
    rescued exactly like one in Yellowknife would be — the lane has never
    looked at `row.location` and this pins that it still doesn't.
    """
    toronto_row = PipelineRow(
        url="https://example.invalid/toronto-high-score",
        company="Foundry",
        role="Applied AI Engineer",
        location="Toronto, Ontario",
        posted="2026-08-16",
        source="direct",
    )
    high_score_toronto = Ranked(row=toronto_row, score=8.5, reasons=("applied AI",))
    shortlisted = [_ranked("Foci", 7.5)]

    lane = _overflow_lane([*shortlisted, high_score_toronto], shortlisted)

    assert lane == [high_score_toronto]
