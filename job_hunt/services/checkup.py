"""End-of-session consistency checks.

The tracking loop has one weak joint: an agent builds materials into
``output/`` and the user submits them, and nothing anywhere notices that no
tracker row was ever written. That is how thirty-five applications from
2026-08-19/20 stayed invisible. These checks look at what is on disk and in
the mailbox and say what has no record yet.

Every check is read-only. It reports; the operator records.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.employer_match import EmployerMatcher, load_aliases

# Statuses that mean the application was actually sent.
_SENT_STATUSES = {"Applied", "Rejected", "Interview", "Responded", "Offer"}
# Words in a directory slug that never identify an employer.
_SLUG_NOISE = {
    "handbuilt", "next", "round", "unknown", "employer", "hiring", "at",
    "and", "for", "the", "of", "job", "jobs", "careers", "com", "ca",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    items: list[str] = field(default_factory=list)
    fix: str = ""


def _slug_tokens(name: str) -> set[str]:
    """Alphabetic words in a directory name, minus the date and the hash suffix."""
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name)
    stem = re.sub(r"-[0-9a-f]{8}$", "", stem)
    return {
        token
        for token in re.findall(r"[a-z]+", stem.lower())
        if len(token) > 2 and token not in _SLUG_NOISE
    }


def _dir_date(name: str) -> date | None:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})-", name)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _linked_row(directory: Path) -> str | None:
    """Status of the tracker row this directory was recorded against, if any."""
    marker = directory / ".tracker-row"
    if not marker.exists():
        return None
    try:
        return str(json.loads(marker.read_text(encoding="utf-8")).get("status") or "")
    except (json.JSONDecodeError, OSError):
        return None


def unrecorded_artifacts(
    *,
    since: date,
    output_dir: Path = Path("output"),
    tracker: TrackerRepository | None = None,
    aliases: Mapping[str, str] | None = None,
) -> Check:
    """Directories holding a rendered PDF whose employer has no tracker row.

    This is the check that would have caught the 2026-08-19/20 batch: the
    materials existed on disk for ten days with nothing pointing at them.
    """
    tracker = tracker or TrackerRepository(Path("data/applications.md"))
    matcher = EmployerMatcher(tracker.parse(), aliases=aliases if aliases is not None else load_aliases())

    missing: list[str] = []
    unsent: list[str] = []
    if output_dir.exists():
        for child in sorted(output_dir.iterdir()):
            if not child.is_dir():
                continue
            when = _dir_date(child.name)
            if when is None or when < since:
                continue
            if not any(child.glob("*.pdf")):
                continue
            linked = _linked_row(child)
            if linked is not None:
                # Recorded through `apply --confirmed`, which stamped the row
                # number in. No guessing needed, and no false positive when the
                # directory slug abbreviates the employer ("ccl" vs
                # "Connor, Clark & Lunn Financial Group").
                if linked not in _SENT_STATUSES:
                    unsent.append(f"{child.name} (row exists, status {linked})")
                continue
            tokens = _slug_tokens(child.name)
            if not tokens:
                continue
            hits = [match.entry.status for match in matcher.any_employer(tokens)]
            if not hits:
                missing.append(child.name)
            elif not any(status in _SENT_STATUSES for status in hits):
                unsent.append(f"{child.name} (rows exist, none marked as sent)")

    items = missing + unsent
    return Check(
        name="artifacts without a tracker row",
        ok=not items,
        detail=(
            "every output/ directory with a PDF maps to a tracker row"
            if not items
            else f"{len(missing)} with no row at all, {len(unsent)} whose rows are not marked as sent"
        ),
        items=items,
        fix=(
            "If it was submitted:\n"
            "  job-hunt apply '<url>' --company '...' --role '...' --pdf '<pdf>' "
            "--no-browser --confirmed\n"
            "If it was not, leave it — an evaluated-but-unsent row is normal."
        ),
    )


def event_log_readable() -> Check:
    repo = EmailEventRepository()
    bad = repo.malformed()
    return Check(
        name="event log readable",
        ok=not bad,
        detail=(
            f"{len(repo.list(limit=100000))} events readable"
            if not bad
            else f"{len(bad)} unreadable row(s); every inbound command silently skips them"
        ),
        items=[f"line {item.line_number}: {item.reason}" for item in bad],
        fix="job-hunt email verify",
    )


def mailbox_gaps(*, since: str) -> Check:
    from job_hunt.services.email.gaps import find_gaps

    gaps = find_gaps(since=since)
    untracked = [gap for gap in gaps if gap.kind == "untracked"]
    stale = [gap for gap in gaps if gap.kind == "stale_status"]
    return Check(
        name="mailbox agrees with the tracker",
        ok=not gaps,
        detail=(
            "no acknowledgement or rejection is missing a row"
            if not gaps
            else f"{len(untracked)} acknowledged with no row, {len(stale)} row(s) whose mail says closed"
        ),
        items=[f"{gap.date} {gap.category} {gap.company} / {gap.role}" for gap in gaps],
        fix="job-hunt email summarize --since 21d --live && job-hunt email gaps",
    )


def outreach_followups() -> Check:
    from job_hunt.services.outreach import due_events

    due = due_events()
    return Check(
        name="outreach follow-ups",
        ok=not due,
        detail="nothing due" if not due else f"{len(due)} follow-up(s) due",
        items=[f"{event.follow_up_at} {event.company} — {event.role}" for event in due],
        fix="job-hunt outreach due",
    )


def run_checkup(*, days: int = 30, today: date | None = None) -> list[Check]:
    today = today or date.today()
    since_date = today - timedelta(days=days)

    def safe_run_check(check_name: str, check_fn) -> Check:
        try:
            return check_fn()
        except Exception as e:
            return Check(
                name=check_name,
                ok=False,
                detail=f"{type(e).__name__}: {str(e)}",
                fix=f"Check job_hunt/services/checkup.py for {check_name}",
            )

    return [
        safe_run_check("event log readable", event_log_readable),
        safe_run_check("artifacts without a tracker row", lambda: unrecorded_artifacts(since=since_date)),
        safe_run_check("mailbox agrees with the tracker", lambda: mailbox_gaps(since=since_date.isoformat())),
        safe_run_check("outreach follow-ups", outreach_followups),
    ]
