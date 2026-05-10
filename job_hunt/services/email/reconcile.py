from __future__ import annotations

from pydantic import BaseModel
from rapidfuzz import fuzz

from job_hunt.models.events import ApplicationEvent
from job_hunt.models.review import ReviewItem
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.review_repo import ReviewRepository
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository, normalize


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
) -> ReconcileResult:
    event_repo = event_repo or EmailEventRepository()
    tracker = tracker or TrackerRepository()
    review_repo = review_repo or ReviewRepository()
    events = event_repo.list(limit=limit)
    result = ReconcileResult(scanned=len(events))
    for event in events:
        status = EMAIL_STATUS_MAP.get(event.event_type)
        if not status:
            result.skipped += 1
            continue
        matched, match_score = tracker.find_match(company=event.company, role=event.role)
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
    if match_score < 0.75:
        return False
    if normalize(event.role or "") == normalize(matched.role):
        return True
    role_score = fuzz.token_set_ratio(matched.role, event.role or "") / 100
    if normalize(event.company or "") == normalize(matched.company):
        return role_score >= 0.82
    return role_score >= 0.75


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
    generic_companies = {
        "app",
        "ats",
        "careers",
        "candidates",
        "dayforce",
        "gem",
        "hire",
        "hirehive",
        "myworkday",
        "njoyn",
        "pinpoint",
        "pinpointhq",
        "postjobfree",
        "recruiterflowmail",
        "shl",
        "successfactors",
        "vervoe",
    }
    if company.lower() in generic_companies:
        return False
    bad_fragments = [
        "thank you",
        "application",
        "candidate",
        "disclaimer",
        "diversity",
        "this time",
        "we are",
        "we have",
        "your interest",
        "your application",
        "received your",
        "joining our team",
        "submission deadline",
        "will review",
        "job alert",
        "unsubscribe",
    ]
    combined = f"{company} {role}".lower()
    return not any(fragment in combined for fragment in bad_fragments)


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
