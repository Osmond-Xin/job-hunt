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
slots does not break the geography rule, and set the floor at 2.0.

The North is the exception, on the operator's ruling 2026-09-17: a full match
there almost never exists, so those applications are a lottery on technical
reach. Any role he can do on technology alone fills a North slot whatever it
scores, and a role he cannot (a policy or accounting analyst) does not, however
well it scores (``triage.technical_reach``).

The strategy changed on 2026-10-19 (operator: "主要投递边远企业和初创企业"),
after two finished interview loops (Cameron Stephens, Hexalence) ended in
rejection on the same day. Every human conversation so far had come from a
start-up, a remote region, or a mid-size employer outside the GTA; none from a
large corporate. So the slate is now mostly two lanes — remote regions (the
North plus small communities, ``triage.remote_region``) and start-ups (read off
the source board, ``triage.startup_source``) — and the public-sector and
outside-GTA reservations are gone; a public employer in a remote town still
fills the remote-region lane. The remaining slots go to the best match overall.

A reservation that cannot be filled is reported, with whether the inbox had the
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

from job_hunt.services.triage import REGIONS, region, remote_region, sector, startup_source, technical_reach

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


@dataclass(frozen=True)
class Reservation:
    name: str
    share: float  # of the slate, rounded down so small slates keep room for the best overall
    belongs: Callable[[str, str, str], bool]  # (company, location, source)
    # Filled on technical reach instead of the score floor (see module docstring).
    lottery: bool = False


RESERVATIONS: tuple[Reservation, ...] = (
    # Scarcest bucket first. Remote regions are a lottery on technical reach,
    # as the North alone was from 2026-09-17: a full match there almost never
    # exists. Start-ups are plentiful, so they keep the score floor.
    Reservation("remote region", 0.4, lambda _company, location, _source: remote_region(location), lottery=True),
    Reservation("start-up", 0.4, lambda _company, _location, source: startup_source(source)),
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
    role: Callable[[T], str],
    source: Callable[[T], str],
    inbox: Sequence[T] | None = None,
    min_score: float = MIN_SCORE,
    reservations: Sequence[Reservation] = RESERVATIONS,
) -> tuple[list[T], list[ReservationGap]]:
    """Pick ``limit`` items from ``items`` (already in preference order).

    Each reservation takes the best eligible items not yet chosen. The
    remaining slots go to the best items overall. The slate keeps the input
    order, so whatever ranking the caller applied (score, or model fit) still
    decides where each item appears — and a later stage can choose again from
    it. Grouping by lane is a display step, done once at the end (``by_lane``):
    grouping here made ``--verify`` re-choose from a lane-ordered list, read the
    remote rows as the "best overall" and drop the real ones (Codex red team
    2026-10-24).
    ``inbox`` is what a gap is measured against (defaults to ``items``).
    """
    if limit <= 0 or not items:
        return [], []
    chosen: set[int] = set()
    gaps: list[ReservationGap] = []

    for reservation in reservations:
        def eligible(item: T) -> bool:
            if reservation.lottery:
                return technical_reach(role(item))
            return score(item) >= min_score

        def belongs(item: T) -> bool:
            return reservation.belongs(company(item), location(item), source(item))

        wanted = math.floor(limit * reservation.share)
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


BEST_OVERALL = "best overall"
LANE_ORDER = (*(reservation.name for reservation in RESERVATIONS), BEST_OVERALL)


def lane_of(
    company: str, location: str, source: str, reservations: Sequence[Reservation] = RESERVATIONS
) -> str:
    """The first reservation a row belongs to, or ``BEST_OVERALL``."""
    for reservation in reservations:
        if reservation.belongs(company, location, source):
            return reservation.name
    return BEST_OVERALL


def harvest_line(rows: Sequence[dict]) -> str:
    """The mix of a harvest script's kept rows, with why it is lopsided.

    The Adzuna harvests search AI phrases only, so they are private sector and
    metro-heavy by construction. On 2026-09-16 they were the whole daily list
    and public and northern work had silently vanished; each run now says so.
    """
    mix = Mix.of([(row.get("company", ""), row.get("location", "")) for row in rows])
    return f"balance of kept: {mix.line()} — AI-phrase lane only; `job-hunt triage` is the balanced inbox"


def by_lane(items: Sequence[T], lane: Callable[[T], str]) -> list[T]:
    """Remote region, then start-up, then the rest; input order within a lane.

    The operator reads the list from the top (2026-10-24: "下次优先从边远和初创
    读取。然后再是现在的"), so the lanes the strategy rests on come first.
    """
    return sorted(items, key=lambda item: LANE_ORDER.index(lane(item)))
