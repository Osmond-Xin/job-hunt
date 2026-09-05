from __future__ import annotations

from pydantic import BaseModel

from job_hunt.models.events import ApplicationEvent
from job_hunt.repositories.email_decision_repo import EmailDecisionRepository, EmailEventDecision
from job_hunt.repositories.email_event_repo import EmailEventRepository
from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository
from job_hunt.services.email.reconcile import EMAIL_STATUS_MAP, _is_reliable_tracker_match
from job_hunt.services.employer_match import EmployerMatcher


class EmailApprovalResult(BaseModel):
    event: ApplicationEvent
    tracker_entry: TrackerEntry | None = None
    matched_existing: TrackerEntry | None = None
    match_score: float = 0.0
    decision_recorded: bool = False


def list_review_candidates(
    *,
    limit: int = 50,
    event_repo: EmailEventRepository | None = None,
    decision_repo: EmailDecisionRepository | None = None,
) -> list[ApplicationEvent]:
    event_repo = event_repo or EmailEventRepository()
    decision_repo = decision_repo or EmailDecisionRepository()
    decided = decision_repo.decided_event_ids()
    candidates = [
        event
        for event in event_repo.list(limit=100000)
        if event.id not in decided and (event.needs_review or not event.company or not event.role)
    ]
    return candidates[-limit:]


def approve_email_event(
    event_id: str,
    *,
    company: str | None = None,
    role: str | None = None,
    status: str | None = None,
    note: str = "Approved from Gmail review",
    force_new: bool = False,
    event_repo: EmailEventRepository | None = None,
    decision_repo: EmailDecisionRepository | None = None,
    tracker: TrackerRepository | None = None,
) -> EmailApprovalResult:
    event_repo = event_repo or EmailEventRepository()
    decision_repo = decision_repo or EmailDecisionRepository()
    tracker = tracker or TrackerRepository()
    event = event_repo.find_by_prefix(event_id)
    if not event:
        raise ValueError(f"Email event not found: {event_id}")

    resolved_company = (company or event.company or "").strip()
    resolved_role = (role or event.role or "").strip()
    resolved_status = (status or EMAIL_STATUS_MAP.get(event.event_type) or "").strip()
    if not resolved_company or not resolved_role or not resolved_status:
        raise ValueError("Approval requires company, role, and status.")

    matched, match_score = EmployerMatcher(tracker.parse()).raw_match(company=resolved_company, role=resolved_role)
    if matched and not force_new and _is_reliable_tracker_match(
        event.model_copy(update={"company": resolved_company, "role": resolved_role}),
        matched,
        match_score,
    ):
        decision_repo.append(
            EmailEventDecision(
                event_id=event.id,
                decision="approved",
                note=f"Matched existing tracker row #{matched.number}",
            )
        )
        return EmailApprovalResult(
            event=event,
            matched_existing=matched,
            match_score=match_score,
            decision_recorded=True,
        )

    entry = tracker.add_imported_email_entry(
        company=resolved_company,
        role=resolved_role,
        status=resolved_status,
        email_ref=f"email:{event.source_message_id or event.id}",
        note=f"{note}; source event {event.event_type}",
    )
    decision_repo.append(
        EmailEventDecision(
            event_id=event.id,
            decision="approved",
            note=f"Imported tracker row #{entry.number}",
        )
    )
    return EmailApprovalResult(event=event, tracker_entry=entry, match_score=match_score, decision_recorded=True)


def ignore_email_event(
    event_id: str,
    *,
    note: str = "",
    event_repo: EmailEventRepository | None = None,
    decision_repo: EmailDecisionRepository | None = None,
) -> ApplicationEvent:
    event_repo = event_repo or EmailEventRepository()
    decision_repo = decision_repo or EmailDecisionRepository()
    event = event_repo.find_by_prefix(event_id)
    if not event:
        raise ValueError(f"Email event not found: {event_id}")
    decision_repo.append(EmailEventDecision(event_id=event.id, decision="ignored", note=note))
    return event
