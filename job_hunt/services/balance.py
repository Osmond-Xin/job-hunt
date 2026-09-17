"""Keep the day's shortlist from collapsing into one kind of job.

Measured 2026-09-16, after the operator noticed that remote-region and public
sector roles had vanished: the pending inbox held 402 public-sector and 95
northern rows, yet triage's top 30 was 53% Greater Toronto and had no northern
row at all. Nothing excluded them. They lost on supply, one row at a time:

- Toronto carries roughly ten times the postings of any other Canadian market,
  so a pure score sort fills every slot from the largest market;
- the role vocabulary was written for private-sector titles, so a Government of
  Yukon "Functional Analyst" scored 2.0 and ranked around #400 (the vocabulary
  is widened in triage.py; this module is the other half);
- since 2026-09-03 geography scores nothing (the operator's ruling — job match
  first), so nothing lifted them either.

So the shortlist is chosen, not merely sorted: slots are reserved for the North,
for public-sector work and for work outside the GTA — filled only from rows that
already score as a match (at least ``MIN_SCORE``), otherwise left to the ranking.
The score itself is untouched; the operator ruled 2026-09-16 that reserving
slots does not break the geography rule, and set the floor at 2.0. A
reservation that cannot be filled is reported, with whether the inbox had the
rows at all — a sourcing gap and a row lost to the model screen or a dead link
call for different fixes.

Where a row is, and who employs it, are read by ``triage.region`` and
``triage.sector``, beside the other location and employer vocabularies.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Sequence, TypeVar

from job_hunt.services.triage import REGIONS, region, sector

T = TypeVar("T")

MIN_SCORE = 2.0

REGION_LABELS = {
    "gta": "GTA",
    "ontario": "Ontario ex-GTA",
    "other_province": "other provinces",
    "north": "North",
    "remote": "remote",
    "unknown": "no location",
}


def outside_gta(location: str) -> bool:
    """A named place that is not the GTA. Remote and unknown rows do not count."""
    return region(location) in {"ontario", "other_province", "north"}


@dataclass(frozen=True)
class Reservation:
    name: str
    share: float  # of the slate, rounded up
    belongs: Callable[[str, str], bool]  # (company, location)


RESERVATIONS: tuple[Reservation, ...] = (
    # Smallest bucket first, so a Yukon government row is spent where it is scarcest.
    Reservation("North", 0.1, lambda _company, location: region(location) == "north"),
    Reservation("public sector", 0.3, lambda company, _location: sector(company) == "public"),
    Reservation("outside GTA", 0.5, lambda _company, location: outside_gta(location)),
)


@dataclass(frozen=True)
class Mix:
    regions: Counter = field(default_factory=Counter)
    sectors: Counter = field(default_factory=Counter)
    total: int = 0

    @classmethod
    def of(cls, pairs: Sequence[tuple[str, str]]) -> "Mix":
        """``pairs`` are ``(company, location)``."""
        return cls(
            regions=Counter(region(location) for _company, location in pairs),
            sectors=Counter(sector(company) for company, _location in pairs),
            total=len(pairs),
        )

    def line(self) -> str:
        if not self.total:
            return "empty"
        regions = " · ".join(
            f"{REGION_LABELS[key]} {self.regions[key]}" for key in REGIONS if self.regions[key]
        )
        return f"{regions} | public {self.sectors['public']} / private {self.sectors['private']}"


@dataclass(frozen=True)
class ReservationGap:
    reservation: str
    wanted: int
    got: int
    in_inbox: int  # eligible rows of this kind anywhere in the ranked inbox

    @property
    def sourcing(self) -> bool:
        """The inbox itself is short, as opposed to rows lost after ranking."""
        return self.in_inbox < self.wanted


def balanced_slate(
    items: Sequence[T],
    limit: int,
    *,
    company: Callable[[T], str],
    location: Callable[[T], str],
    score: Callable[[T], float],
    inbox: Sequence[T] | None = None,
    min_score: float = MIN_SCORE,
    reservations: Sequence[Reservation] = RESERVATIONS,
) -> tuple[list[T], list[ReservationGap]]:
    """Pick ``limit`` items from ``items`` (already in preference order).

    Each reservation takes the best eligible items not yet chosen; a Yukon
    government row counts toward all three. The remaining slots go to the best
    items overall. The slate keeps the input order, so whatever ranking the
    caller applied (score, or model fit) still decides where each item appears.
    ``inbox`` is what a gap is measured against (defaults to ``items``).
    """
    if limit <= 0 or not items:
        return [], []
    chosen: set[int] = set()
    gaps: list[ReservationGap] = []

    def eligible(item: T) -> bool:
        return score(item) >= min_score

    for reservation in reservations:
        def belongs(item: T) -> bool:
            return reservation.belongs(company(item), location(item))

        wanted = math.ceil(limit * reservation.share)
        have = sum(1 for index in chosen if belongs(items[index]))
        for index, item in enumerate(items):
            if have >= wanted or len(chosen) >= limit:
                break
            if index not in chosen and eligible(item) and belongs(item):
                chosen.add(index)
                have += 1
        if have < wanted:
            available = sum(1 for item in (items if inbox is None else inbox) if eligible(item) and belongs(item))
            gaps.append(ReservationGap(reservation.name, wanted, have, available))

    for index in range(len(items)):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    return [items[index] for index in sorted(chosen)], gaps


def harvest_line(rows: Sequence[dict]) -> str:
    """The mix of a harvest script's kept rows, with why it is lopsided.

    The Adzuna harvests search AI phrases only, so they are private sector and
    metro-heavy by construction. On 2026-09-16 they were the whole daily list
    and public and northern work had silently vanished; each run now says so.
    """
    mix = Mix.of([(row.get("company", ""), row.get("location", "")) for row in rows])
    return f"balance of kept: {mix.line()} — AI-phrase lane only; `job-hunt triage` is the balanced inbox"
