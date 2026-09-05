"""Cross-reference LLM mailbox summaries against the tracker.

``email summarize`` writes high-quality classifications to
``data/email-summaries.jsonl``, but nothing reads them back — so an
acknowledgement for an application that was never recorded, or a rejection for
a row still marked Applied, stays invisible. This module reports those gaps.

It is deliberately read-only. An email body is untrusted input (see
``docs/design-notes.md`` P1), so the operator decides what to record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository
from job_hunt.services.email.summarize import SUMMARY_PATH
from job_hunt.services.employer_match import EmployerMatcher, load_aliases

# Categories that mean "this application exists".
_APPLIED_CATEGORIES = {"application_ack", "interview_invite", "offer", "rejection"}
# Categories that mean the tracker status is stale if it still says Applied.
_CLOSED_CATEGORIES = {"rejection"}
# Mail that says the application moved forward, not that one is missing.
_ADVANCE_CATEGORIES = {"interview_invite", "offer"}
_ADVANCED_STATUSES = {"Interview", "Offer", "Responded"}
# Statuses that already record an outcome, so a rejection mail is not news.
_SETTLED_STATUSES = {"Rejected", "Discarded", "SKIP", "Withdrawn", "Offer"}


@dataclass
class Gap:
    kind: str  # "untracked" | "stale_status" | "advance"
    date: str
    company: str
    role: str
    category: str
    subject: str
    entry: TrackerEntry | None = None


def _load_summaries(path: Path, since: str) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("job_related") or not row.get("company"):
            continue
        if row.get("category") not in _APPLIED_CATEGORIES:
            continue
        if (row.get("date") or "")[:10] < since:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


def find_gaps(
    *,
    since: str,
    summary_path: Path = SUMMARY_PATH,
    tracker: TrackerRepository | None = None,
) -> list[Gap]:
    """Applications the mailbox knows about that the tracker does not."""
    tracker = tracker or TrackerRepository(Path("data/applications.md"))
    aliases = load_aliases()
    # Read-only: the tracker never changes mid-run, so one snapshot is safe
    # and avoids re-parsing applications.md per summary row.
    matcher = EmployerMatcher(tracker.parse(), aliases=aliases)
    gaps: list[Gap] = []
    seen_untracked: set[str] = set()
    for row in _load_summaries(summary_path, since):
        company = matcher.resolve_alias(row["company"])
        role = row.get("role") or ""
        match = matcher.best(company=company, role=role, intent="report")
        entry = match.entry if match else None
        matched = entry is not None
        if not matched and row.get("category") in _ADVANCE_CATEGORIES:
            # An interview invitation from an employer already in the tracker is
            # not a missing application — it is a status the row has not caught
            # up with. Reporting it as "untracked" is how a real advance from
            # GNWT sat unnoticed for eleven days among forty-six false alarms.
            # Pass role=None regardless of whether one was given: this forces
            # the matcher's company-only fallback, which is the only one that
            # ignores a role mismatch entirely.
            advance_match = matcher.best(company=company, role=None, intent="report")
            entry = advance_match.entry if advance_match else None
            if entry is not None:
                if entry.status not in _ADVANCED_STATUSES | _SETTLED_STATUSES:
                    gaps.append(
                        Gap(
                            kind="advance",
                            date=(row.get("date") or "")[:10],
                            company=company,
                            role=role,
                            category=row.get("category") or "",
                            subject=(row.get("subject") or "")[:80],
                            entry=entry,
                        )
                    )
                continue
        if not matched:
            # One row per company/role, not one per acknowledgement email.
            key = (company + "|" + role).lower()
            if key in seen_untracked:
                continue
            seen_untracked.add(key)
            gaps.append(
                Gap(
                    kind="untracked",
                    date=(row.get("date") or "")[:10],
                    company=company,
                    role=role,
                    category=row.get("category") or "",
                    subject=(row.get("subject") or "")[:80],
                )
            )
            continue
        if row.get("category") in _CLOSED_CATEGORIES and entry.status not in _SETTLED_STATUSES:
            gaps.append(
                Gap(
                    kind="stale_status",
                    date=(row.get("date") or "")[:10],
                    company=company,
                    role=role,
                    category=row.get("category") or "",
                    subject=(row.get("subject") or "")[:80],
                    entry=entry,
                )
            )
    return gaps
