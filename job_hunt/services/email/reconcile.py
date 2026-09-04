from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel

from job_hunt.models.events import ApplicationEvent
from job_hunt.models.review import ReviewItem
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.review_repo import ReviewRepository
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository
from job_hunt.services.email.message_parser import BAD_FRAGMENTS, GENERIC_COMPANIES
from job_hunt.services.employer_match import EmployerMatcher, is_reliable_match, load_aliases
from job_hunt.services.tracker_ops import _STATUS_RANK


EMAIL_STATUS_MAP = {
    "application_submitted": "Applied",
    "application_received": "Applied",
    "recruiter_reply": "Responded",
    "assessment": "Responded",
    "interview": "Interview",
    "offer": "Offer",
    "rejection": "Rejected",
}

# tracker_ops._STATUS_RANK is the one place the tracker's status order is
# defined; reconcile reuses it rather than keeping a second, driftable copy.
# Ranks 0/1 are its terminal states (its own comment: "Active applications
# outrank terminal states") -- SKIP, Discarded, Rejected. "Contacted" (a real
# status in templates/states.yml) has no entry at all, so `.get()` on it
# returns None, not 0 -- see `_transition_kind`, which treats that as
# "unranked" rather than silently letting it sort below SKIP/Discarded.
_TERMINAL_STATUSES = frozenset(name for name, rank in _STATUS_RANK.items() if rank <= 1)

# How much of data/email-events.jsonl to read on a plain `email reconcile`.
# The old default (50) hid everything older than the last 50 events against
# a 327-event log; a full scan takes seconds, so the default now covers any
# realistic log size and `--limit` narrows it only when asked.
_DEFAULT_LIMIT = 100_000


class ReconcileResult(BaseModel):
    scanned: int = 0
    total_available: int = 0
    matched: int = 0
    updated: int = 0
    regressed: int = 0
    unranked: int = 0
    imported: int = 0
    review_created: int = 0
    skipped: int = 0


def _transition_kind(current_status: str, new_status: str) -> str:
    """Classify a proposed status change using tracker_ops._STATUS_RANK.

    _STATUS_RANK ranks terminal outcomes low (SKIP/Discarded=0, Rejected=1) --
    right for its own dedup use ("an Interview duplicate beats a Rejected
    one"), but not a temporal order by itself: Applied (3) outranks Rejected
    (1), so a bare "new_rank > current_rank" check would wave through
    Rejected -> Applied as an "advance". That is exactly the regression the
    live corpus showed (5 of the 8 backwards rows). So:

    - a terminal current status is a sink: once Rejected/Discarded/SKIP,
      no mail event moves it further, regardless of the new status's rank.
    - moving *into* a terminal status is always a legitimate advance from any
      still-open stage, regardless of its own low rank number -- a rejection
      ends the funnel, it doesn't demote it.
    - otherwise, advance only if the new status outranks the current one.

    Returns "advance", "backward", or "unranked" (current_status has no
    entry in _STATUS_RANK at all, e.g. "Contacted" -- direction can't be
    inferred, and this is never silently treated as rank 0).
    """
    if current_status in _TERMINAL_STATUSES:
        return "backward"
    current_rank = _STATUS_RANK.get(current_status)
    if current_rank is None:
        return "unranked"
    if new_status in _TERMINAL_STATUSES:
        return "advance"
    new_rank = _STATUS_RANK[new_status]  # every EMAIL_STATUS_MAP value is ranked
    return "advance" if new_rank > current_rank else "backward"


def reconcile_email_events(
    *,
    apply: bool = False,
    limit: int = _DEFAULT_LIMIT,
    import_new: bool = False,
    update_existing: bool = True,
    create_review: bool = True,
    event_repo: EmailEventRepository | None = None,
    tracker: TrackerRepository | None = None,
    review_repo: ReviewRepository | None = None,
    aliases: Mapping[str, str] | None = None,
) -> ReconcileResult:
    event_repo = event_repo or EmailEventRepository()
    tracker = tracker or TrackerRepository()
    review_repo = review_repo or ReviewRepository()
    aliases = aliases if aliases is not None else load_aliases()
    # Read the whole log regardless of `limit`, so `total_available` always
    # reflects the true size and the report can say whether this run's
    # window (`scanned`) covered all of it or only part.
    all_events = event_repo.list(limit=10**9)
    events = all_events[-limit:]
    result = ReconcileResult(scanned=len(events), total_available=len(all_events))
    for event in events:
        status = EMAIL_STATUS_MAP.get(event.event_type)
        if not status:
            result.skipped += 1
            continue
        # Re-read per event, not once for the whole batch: `apply` mutates the
        # tracker mid-loop (update_entry / add_imported_email_entry below), and
        # a later event in the same batch must see those writes — the same
        # freshness `tracker.find_match` gave every caller before this change.
        matcher = EmployerMatcher(tracker.parse(), aliases=aliases)
        matched, match_score = matcher.raw_match(company=event.company, role=event.role)
        # `EmployerMatcher.best(intent="mutate")` is the general "a human
        # already made this identification" gate (find_match's own 0.70
        # threshold, no more). Nobody is in the loop here, so reconcile keeps
        # its own extra floor as an explicit check on top of the raw match —
        # this is reconcile's policy, not something `mutate` imposes for it.
        if matched and event.role and _is_reliable_tracker_match(event, matched, match_score):
            result.matched += 1
            if update_existing and matched.status != status:
                kind = _transition_kind(matched.status, status)
                if kind == "advance":
                    # Counted here regardless of `apply`, so a dry run reports
                    # the same number `--apply` will actually touch.
                    result.updated += 1
                    if apply:
                        tracker.update_entry(matched.model_copy(update={"status": status}))
                else:
                    # A backward or unranked transition is not an update — it
                    # contradicts the row's current state, which is worth a
                    # human's eyes rather than a silent drop. Surface it
                    # through the same review queue low-confidence matches
                    # already use, instead of a mutation.
                    if kind == "backward":
                        result.regressed += 1
                        reason = f"row #{matched.number} is already {matched.status}; mail says {status}"
                    else:
                        result.unranked += 1
                        reason = f"row #{matched.number} status {matched.status!r} has no rank to compare against"
                    if create_review:
                        result.review_created += 1
                        if apply:
                            review_repo.append(
                                build_review_item(
                                    event,
                                    status=status,
                                    match_score=match_score,
                                    matched_id=matched.number,
                                    item_type="status_update_conflict",
                                    reason=reason,
                                )
                            )
            continue
        if import_new and _safe_import_candidate(event):
            if apply:
                tracker.add_imported_email_entry(
                    company=event.company,
                    role=event.role,
                    status=status,
                    email_ref=f"email:{event.source_message_id or event.id}",
                    note=f"Imported from Gmail event {event.event_type}; needs evaluation",
                )
            result.imported += 1
            continue
        if apply and create_review:
            review_repo.append(
                build_review_item(
                    event,
                    status=status,
                    match_score=match_score,
                    matched_id=matched.number if matched else None,
                )
            )
        if create_review:
            result.review_created += 1
        else:
            result.skipped += 1
    return result


def _is_reliable_tracker_match(event: ApplicationEvent, matched: TrackerEntry, match_score: float) -> bool:
    """reconcile's extra floor on top of a raw find_match-style score,
    applied because reconcile acts on inbound mail with nobody in the loop —
    thin wrapper around ``employer_match.is_reliable_match``, kept as a
    module-level function (rather than inlined) because ``email/review.py``
    still imports this by name.
    """
    return is_reliable_match(
        company=event.company,
        role=event.role,
        matched_company=matched.company,
        matched_role=matched.role,
        score=match_score,
    )


def _safe_import_candidate(event: ApplicationEvent) -> bool:
    if (
        not event.company
        or not event.role
        or event.confidence < 0.85
        or event.needs_review
        or event.event_type not in EMAIL_STATUS_MAP
    ):
        return False
    company = event.company.strip()
    role = event.role.strip()
    if len(company) > 60 or len(role) > 100:
        return False
    if len(company.split()) > 6 or len(role.split()) > 14:
        return False
    if company.lower() in GENERIC_COMPANIES:
        return False
    combined = f"{company} {role}".lower()
    return not any(fragment in combined for fragment in BAD_FRAGMENTS)


def build_review_item(
    event: ApplicationEvent,
    *,
    status: str,
    match_score: float,
    matched_id: int | None,
    item_type: str = "email_match_low_confidence",
    reason: str | None = None,
) -> ReviewItem:
    summary = (
        f"Review Gmail event for {event.company or 'unknown company'} / "
        f"{event.role or 'unknown role'} -> {status}"
    )
    if reason:
        summary = f"{summary} ({reason})"
    return ReviewItem(
        type=item_type,
        priority="normal",
        summary=summary,
        proposed_action={
            "event_id": event.id,
            "event_type": event.event_type,
            "company": event.company,
            "role": event.role,
            "proposed_status": status,
            "matched_tracker_id": matched_id,
            "match_score": round(match_score, 3),
        },
        evidence=[event.subject, event.snippet, *event.evidence],
    )
