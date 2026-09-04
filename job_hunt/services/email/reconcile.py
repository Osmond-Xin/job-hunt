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


EMAIL_STATUS_MAP = {
    "application_submitted": "Applied",
    "application_received": "Applied",
    "recruiter_reply": "Responded",
    "assessment": "Responded",
    "interview": "Interview",
    "offer": "Offer",
    "rejection": "Rejected",
}


class ReconcileResult(BaseModel):
    scanned: int = 0
    matched: int = 0
    updated: int = 0
    imported: int = 0
    review_created: int = 0
    skipped: int = 0


def reconcile_email_events(
    *,
    apply: bool = False,
    limit: int = 50,
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
    events = event_repo.list(limit=limit)
    result = ReconcileResult(scanned=len(events))
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
            if apply and update_existing and matched.status != status:
                tracker.update_entry(matched.model_copy(update={"status": status}))
                result.updated += 1
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
) -> ReviewItem:
    return ReviewItem(
        type="email_match_low_confidence",
        priority="normal",
        summary=(
            f"Review Gmail event for {event.company or 'unknown company'} / "
            f"{event.role or 'unknown role'} -> {status}"
        ),
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
