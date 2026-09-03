"""The single point where a raw board/API row becomes a job record.

Fifteen places independently turned an HTTP response into a job record before
this module existed: four tier-1 ATS parsers in ``scan.py`` built
``ScannedJob`` directly, and ten board parsers in ``gov_boards.py``,
``regional_boards.py``, ``jobbank.py``, ``adzuna.py`` and ``workday_boards.py``
each returned a bare ``dict[str, str]`` with no schema. A field-name mismatch
between two untyped dicts could not be caught by anything — three of those
mappers silently dropped the posting date until that was fixed by hand.

``JobPosting`` takes ``ScannedJob``'s fields as its starting point (see
``scan.py``) and adds ``source_id`` so a row can say which adapter produced
it. It is frozen: it is the normalised value a mapper hands upstream, not the
mutable record ``scan.py`` then carries through dedup/filtering (that stays
``ScannedJob``, built from a ``JobPosting`` once ``from_row`` accepts it).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class JobPosting:
    url: str
    title: str
    company: str
    portal: str
    source: str
    # Which adapter produced this row (e.g. "workday", "adzuna", "gnwt") —
    # distinct from ``source``, which is a per-row human-readable audit string
    # ("workday Home Depot", "adzuna AI Engineer").
    source_id: str
    location: str = ""
    status: str = "new"
    # Public-sector competitions close on a hard date and stop accepting
    # applications that day. The boards publish it; dropping it meant the
    # pipeline could not tell a posting with two days left from a fresh one.
    closes: str = ""
    # Freshness is one of the four axes triage ranks on, and it reads the date
    # off the pipeline line. Every board row carried a blank one, so the whole
    # direct-board corpus scored as undated and tied at the top.
    posted: str = ""


def from_row(row: Mapping[str, str], *, source_id: str, portal: str) -> JobPosting | None:
    """None for a row with no usable title or URL — the guard that is currently
    copy-pasted at four call sites with a fifth variant.

    ``row`` is expected to already carry any per-source formatting (the
    ``source`` audit string, a company fallback other than "Unknown", a
    posted/closes date already normalised to ISO) — this function only
    applies the guard and the generic field mapping every source shares.
    """
    title = (row.get("title") or "").strip()
    url = (row.get("url") or "").strip()
    if not title or not url:
        return None
    return JobPosting(
        url=url,
        title=title,
        company=(row.get("company") or "Unknown").strip() or "Unknown",
        location=row.get("location", ""),
        portal=portal,
        source=row.get("source", ""),
        closes=row.get("closes", ""),
        posted=row.get("posted", ""),
        source_id=source_id,
    )


@dataclass(frozen=True)
class SourceHealth:
    source_id: str
    ok: bool
    collected: int = 0
    advertised: int | None = None
    truncated: bool = False
    errors: int = 0
    note: str = ""

    def warnings(self) -> list[str]:
        """Turn this source's own numbers into operator-visible warnings.

        See ``scan.py``'s ``_board_coverage_warnings``, which this mirrors:
        a source that returned nothing because it failed reads exactly like a
        source having a quiet week unless something surfaces the difference.
        """
        out: list[str] = []
        if self.errors:
            out.append(
                f"{self.source_id}: {self.errors} failed request(s) — "
                f"{self.collected} postings may be an undercount, not a quiet source"
            )
        if self.truncated:
            short = f", source advertises {self.advertised}" if self.advertised else ""
            out.append(
                f"{self.source_id}: page budget ran out with rows still coming "
                f"({self.collected} collected{short}) — raise max_pages"
            )
        return out


@dataclass(frozen=True)
class SourceResult:
    postings: list[JobPosting]
    health: SourceHealth
