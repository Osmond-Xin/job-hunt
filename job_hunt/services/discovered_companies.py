"""Companies the channels found, fed back into the direct-ATS tier.

A channel (a Getro portfolio board, an aggregator) shows a company only through
the postings that matched a search query and fitted inside a page budget. On
2026-09-21 the Getro tier held 4,155 Canada postings and the scan collected
2,169; a company reached that way is seen through a keyhole. But every such row
links to the company's own ATS board, whose slug is in the URL — and reading
that board directly returns *all* of its postings, including the ones no query
matched. Beacon Software's fitting role was titled plain "Software Engineer".

That day 171 distinct company boards had passed through ``data/pipeline.md`` and
112 of them were tracked nowhere. The hand-curated ``tracked_companies`` list
grows only when somebody thinks of a name; this grows with the channels.

Discovered companies live in ``data/discovered-companies.yml``, not in
``config/portals.yml``: the config is hand-written and commented, and a machine
appending to it would bury that. Anything already in the config — *including an
entry switched off with ``enabled: false``*, which is how an employer closed
under one-role-per-employer is recorded — is never re-added here.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

DISCOVERED_PATH = Path("data/discovered-companies.yml")

# (host pattern, canonical careers_url) for the ATSs the direct tier can read.
_BOARD_RE = re.compile(
    r"https?://(?P<host>jobs\.ashbyhq\.com|boards\.greenhouse\.io|job-boards\.greenhouse\.io|"
    r"jobs\.lever\.co|apply\.workable\.com)/(?P<slug>[A-Za-z0-9][A-Za-z0-9_.-]*)",
    re.I,
)
# Path segments that are not a company: greenhouse embeds, workable's /j/ links.
_NOT_A_COMPANY = frozenset({"embed", "j", "api", "jobs", "v1"})
_CANONICAL = {
    "jobs.ashbyhq.com": "https://jobs.ashbyhq.com/{slug}",
    "boards.greenhouse.io": "https://job-boards.greenhouse.io/{slug}",
    "job-boards.greenhouse.io": "https://job-boards.greenhouse.io/{slug}",
    "jobs.lever.co": "https://jobs.lever.co/{slug}",
    "apply.workable.com": "https://apply.workable.com/{slug}/",
}
_ROW_RE = re.compile(r"^- \[[ x!]\] (?P<rest>.+)$")


def board_key(url: str) -> tuple[str, str] | None:
    """(ats family, slug) for a URL on a readable ATS, else None."""
    match = _BOARD_RE.search(url or "")
    if not match:
        return None
    slug = match.group("slug").lower()
    if slug in _NOT_A_COMPANY:
        return None
    host = match.group("host").lower()
    family = "greenhouse" if "greenhouse" in host else host
    return family, slug


def _careers_url(url: str) -> str:
    match = _BOARD_RE.search(url)
    assert match is not None
    return _CANONICAL[match.group("host").lower()].format(slug=match.group("slug"))


def harvest(pipeline_text: str, known: Iterable[dict[str, Any]], *, today: dt.date | None = None) -> list[dict[str, Any]]:
    """New company boards named in pipeline rows and tracked nowhere yet."""
    stamp = (today or dt.date.today()).isoformat()
    seen = {key for item in known if (key := board_key(item.get("careers_url", "")))}
    found: list[dict[str, Any]] = []
    for line in pipeline_text.splitlines():
        row = _ROW_RE.match(line)
        if not row:
            continue
        parts = [part.strip() for part in row.group("rest").split(" | ")]
        key = board_key(parts[0])
        if key is None or key in seen:
            continue
        seen.add(key)
        source = next((p.removeprefix("source:").strip() for p in parts if p.startswith("source:")), "")
        found.append(
            {
                "name": parts[1] if len(parts) > 1 and parts[1] else key[1],
                "careers_url": _careers_url(parts[0]),
                "discovered_via": source or "pipeline",
                "first_seen": stamp,
                "enabled": True,
            }
        )
    return found


def load(path: Path = DISCOVERED_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [item for item in data if isinstance(item, dict) and item.get("careers_url")]


def merge_into(tracked: list[dict[str, Any]], discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The hand-written list, then every discovered board it does not already name."""
    seen = {key for item in tracked if (key := board_key(item.get("careers_url", "")))}
    extra = []
    for item in discovered:
        key = board_key(item.get("careers_url", ""))
        if key is not None and key not in seen:
            seen.add(key)
            extra.append(item)
    return list(tracked) + extra


def record(new: list[dict[str, Any]], path: Path = DISCOVERED_PATH) -> int:
    """Append newly harvested boards to the data file. Returns how many were added."""
    if not new:
        return 0
    existing = load(path)
    merged = merge_into(existing, new)
    added = len(merged) - len(existing)
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Company boards the discovery channels surfaced, read directly by the scan's\n"
            "# tier 1. Written by job_hunt/services/discovered_companies.py - safe to edit:\n"
            "# set `enabled: false` to stop scanning one; delete nothing, or it comes back.\n"
        )
        path.write_text(header + yaml.safe_dump(merged, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return added
