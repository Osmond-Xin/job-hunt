"""Keep the day's shortlist from collapsing into one kind of job.

Measured 2026-09-16, after the operator noticed that remote-region and public
sector roles had vanished: the pending inbox held 402 public-sector and 95
northern rows, yet triage's top 30 was 53% Greater Toronto and had no northern
row at all. Nothing excluded them. They lost on supply, one row at a time:

- Toronto carries roughly ten times the postings of any other Canadian market,
  so a pure score sort fills every slot from the largest market;
- the score's role vocabulary is written for private-sector titles, so a
  Government of Yukon "Functional Analyst" or a hospital "Technical Analyst —
  Information Systems" scores 2.0 and ranks around #400, behind a plateau of
  3.5s that is hundreds of rows long;
- since 2026-09-03 geography scores nothing (the operator's ruling — job match
  first), so nothing lifted them either.

Each of those is defensible alone. Together they decide the whole list, and
the operator only saw it when he asked. So the shortlist is now chosen, not
merely sorted: a small number of slots are reserved for public-sector work,
for work outside the GTA, and for the North — filled only from rows that are
already a reasonable match (score at or above ``min_score``), and otherwise
left to the ranking. The score itself is untouched. A reservation that cannot
be filled is reported as a shortfall, which is a sourcing problem to fix, not
something to paper over with an off-target row.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Sequence, TypeVar

from job_hunt.services.triage import GOVERNMENT_RE

T = TypeVar("T")

GTA_RE = re.compile(
    r"\b(toronto|mississauga|brampton|markham|vaughan|richmond hill|north york|scarborough|"
    r"etobicoke|oakville|pickering|ajax|thornhill|concord|woodbridge|greater toronto|gta)\b",
    re.I,
)
# Territories, and the northern / remote communities this search has actually
# surfaced (see the northern track: employer in the territory or region, TEER 0-3).
NORTH_RE = re.compile(
    r"\b(yukon|whitehorse|northwest territories|yellowknife|inuvik|hay river|nunavut|iqaluit|"
    r"thunder bay|sudbury|sault ste\.? marie|timmins|north bay|kenora|fort mcmurray|"
    r"grande prairie|prince george|fort st\.? john|labrador|happy valley|thompson|"
    r"flin flon|la ronge|(?:,|\b)\s*(?:yt|nt|nu)\b)",
    re.I,
)
ONTARIO_RE = re.compile(r"\b(ontario|on)\b", re.I)
REMOTE_RE = re.compile(r"\b(remote|telework|télétravail|work from home)\b", re.I)
CHINESE_BOARD_LOCATION_RE = re.compile(r"greater toronto area", re.I)

REGIONS = ("gta", "ontario", "other_province", "north", "remote", "unknown")
REGION_LABELS = {
    "gta": "GTA",
    "ontario": "Ontario ex-GTA",
    "other_province": "other provinces",
    "north": "North",
    "remote": "remote",
    "unknown": "no location",
}


def region(location: str) -> str:
    text = (location or "").strip()
    if not text or text.lower() in {"canada", "ca"}:
        return "unknown"
    if REMOTE_RE.search(text):
        return "remote"
    if NORTH_RE.search(text):
        return "north"
    if GTA_RE.search(text) or CHINESE_BOARD_LOCATION_RE.search(text):
        return "gta"
    if ONTARIO_RE.search(text):
        return "ontario"
    return "other_province"


def sector(company: str) -> str:
    return "public" if GOVERNMENT_RE.search(company or "") else "private"


@dataclass(frozen=True)
class Quotas:
    """Minimum share of the slate, as fractions of ``limit`` (rounded up)."""

    public: float = 0.3
    outside_gta: float = 0.5
    north: float = 0.1
    min_score: float = 1.0


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
class Shortfall:
    reservation: str  # "public" / "outside GTA" / "North"
    wanted: int
    got: int


def balanced_slate(
    items: Sequence[T],
    limit: int,
    *,
    company: Callable[[T], str],
    location: Callable[[T], str],
    score: Callable[[T], float],
    quotas: Quotas = Quotas(),
) -> tuple[list[T], list[Shortfall]]:
    """Pick ``limit`` items from ``items`` (already in preference order).

    Reservations are filled first, smallest bucket first — North, then public
    sector, then outside the GTA — each taking the best eligible items not yet
    chosen; a Yukon government row counts toward all three. The remaining
    slots go to the best items overall. The returned slate keeps the input
    order, so whatever ranking the caller applied (score, or model fit) still
    decides where each item appears.
    """
    if limit <= 0 or not items:
        return [], []
    chosen: set[int] = set()
    shortfalls: list[Shortfall] = []

    def eligible(index: int) -> bool:
        return score(items[index]) >= quotas.min_score

    reservations: list[tuple[str, float, Callable[[int], bool]]] = [
        ("North", quotas.north, lambda i: region(location(items[i])) == "north"),
        ("public", quotas.public, lambda i: sector(company(items[i])) == "public"),
        ("outside GTA", quotas.outside_gta, lambda i: region(location(items[i])) not in {"gta", "unknown", "remote"}),
    ]
    for name, share, belongs in reservations:
        wanted = math.ceil(limit * share) if share > 0 else 0
        have = sum(1 for i in chosen if belongs(i))
        for index in range(len(items)):
            if have >= wanted or len(chosen) >= limit:
                break
            if index in chosen or not eligible(index) or not belongs(index):
                continue
            chosen.add(index)
            have += 1
        if have < wanted:
            shortfalls.append(Shortfall(name, wanted, have))

    for index in range(len(items)):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    return [items[i] for i in sorted(chosen)], shortfalls
