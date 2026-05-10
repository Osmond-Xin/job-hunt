"""Multi-offer comparison service.

Loads selected tracker entries + their reports, renders the
``compare_offers.md`` prompt, and returns the LLM-generated comparison
markdown. Used by the ``job-hunt compare`` CLI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from job_hunt.nodes._prompts import render
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository


_REPORTS_DIR = Path("reports")


@dataclass
class OfferContext:
    """Per-offer context fed to the comparison prompt."""

    tracker_id: int
    company: str
    role: str
    tracker_score: str
    status: str
    date: str
    report: str  # full report markdown, may be empty


def load_offers(
    tracker_ids: list[int],
    apps_path: Path | None = None,
    reports_dir: Path | None = None,
) -> tuple[list[OfferContext], list[str]]:
    """Load tracker rows + report content for the given IDs.

    Returns ``(offers, missing_ids)`` so the CLI can warn about IDs that
    didn't resolve. Order of ``offers`` matches order of ``tracker_ids``.
    """
    apps = apps_path or Path("data/applications.md")
    reports = reports_dir or _REPORTS_DIR
    repo = TrackerRepository(apps)
    by_num: dict[int, TrackerEntry] = {entry.number: entry for entry in repo.parse()}
    offers: list[OfferContext] = []
    missing: list[str] = []
    for tid in tracker_ids:
        entry = by_num.get(tid)
        if entry is None:
            missing.append(str(tid))
            continue
        report_text = _resolve_report(entry, reports)
        offers.append(
            OfferContext(
                tracker_id=entry.number,
                company=entry.company,
                role=entry.role,
                tracker_score=entry.score,
                status=entry.status,
                date=entry.date,
                report=report_text,
            )
        )
    return offers, missing


def _resolve_report(entry: TrackerEntry, reports_dir: Path) -> str:
    """Resolve an entry's report.md content; return '' when missing."""
    raw = entry.report or ""
    match = re.search(r"\(([^)]+\.md)\)", raw)
    candidate = match.group(1) if match else raw.strip().strip("[]")
    if not candidate or candidate.startswith("manual:"):
        return ""
    path = Path(candidate)
    if not path.is_absolute() and not path.exists():
        path = reports_dir / candidate.replace("reports/", "", 1)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def render_prompt(offers: list[OfferContext]) -> str:
    """Render the comparison prompt with the offer list."""
    return render("compare_offers.md", offers=offers)
