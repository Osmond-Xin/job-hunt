"""Find inbound mail a person actually typed, and did not get answered.

The failure this exists for, 2026-09-09/10: a recruiter at CGS sent a Teams
interview invitation for the next morning. It arrived among forty-odd automated
alerts from Indeed, Jobright, Adzuna, LinkedIn and CareerBeacon in the same
week, was never seen, and the slot lapsed. It was the third real interview in
the pipeline. Nothing in the system distinguished it from the noise, because
every inbound reader classifies by *what the mail says* — and an automated job
alert says "interview" as readily as a recruiter does.

This classifies by *who sent it* instead, which is the part a job board cannot
fake.

Two independent rules, because either one alone leaks (measured against the
real mailbox 2026-09-10):

1. **Bulk headers.** ``List-Unsubscribe``, ``Precedence: bulk`` and
   ``Auto-Submitted`` are what a sending platform is obliged to set and a person
   in Outlook never sets. This catches Indeed, Jobright, LinkedIn, GitHub, KAYAK.
2. **No-reply local parts.** ``no-reply@hire.lever.co`` and ``no-reply@coveo.com``
   carry none of those headers and read as human under rule 1 alone. The local
   part is the tell: no human's address is called ``no-reply``.

What is left is mail from people. Most of it still does not matter, so the check
reports only the two kinds that do: mail from a domain belonging to an employer
already in the tracker, and mail whose subject is trying to arrange something.
A check that lists every newsletter is a check nobody reads — and this codebase
has already learned that a check nobody trusts is a check nobody runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from job_hunt.repositories.tracker_repo import TrackerEntry, TrackerRepository
from job_hunt.services.employer_match import normalize

# Address local parts that no person is ever called. Matched on the part before
# "@", case-insensitively, allowing the usual separators (no-reply, noreply,
# no_reply, donotreply) and any prefix, so `CloudPlatform-noreply@google.com`
# and `jobalerts-noreply@linkedin.com` are both caught.
_AUTOMATED_LOCALPART = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|jobalert|job[-_.]?alerts?"
    r"|notification|mailer|bounce|newsletter|digest|invitations"
    r"|donotreply|postmaster|information[-_.]?pusher)",
    re.IGNORECASE,
)

# Headers a bulk sending platform sets and a person's mail client does not.
_BULK_HEADERS = ("list-unsubscribe", "list-id", "auto-submitted")
_BULK_PRECEDENCE = {"bulk", "list", "junk", "auto_reply"}

# Subject words that mean the sender is trying to arrange something with him.
# Deliberately narrow: "opportunity" and "role" appear in every job alert ever
# sent, so they are not here.
_ARRANGING = re.compile(
    r"\b(interview|schedule|scheduling|reschedul\w*|availability|available|"
    r"meeting|invite|invitation|call with|phone screen|next steps?|"
    r"assessment|offer|onboarding|start date|follow[- ]?up)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HumanMessage:
    """One inbound message a person wrote, with why it was surfaced."""

    message_id: str
    thread_id: str
    date: datetime | None
    sender: str
    subject: str
    reason: str
    tracker_row: int | None = None
    tracker_company: str = ""

    @property
    def sort_key(self) -> str:
        return self.date.isoformat() if self.date else ""


def sender_address(sender: str) -> str:
    """The bare address out of a `Display Name <addr@host>` From header."""
    match = re.search(r"<([^>]+)>", sender)
    return (match.group(1) if match else sender).strip().lower()


def sender_domain(sender: str) -> str:
    address = sender_address(sender)
    _, _, domain = address.partition("@")
    return domain.strip().strip(">").lower()


def is_automated(sender: str, headers: dict[str, str]) -> bool:
    """True when a machine sent this, by either independent rule.

    `headers` keys are compared lower-cased; callers may pass them in any case.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    if any(lowered.get(name) for name in _BULK_HEADERS):
        return True
    if lowered.get("precedence", "").strip().lower() in _BULK_PRECEDENCE:
        return True
    local_part = sender_address(sender).partition("@")[0]
    return bool(_AUTOMATED_LOCALPART.search(local_part))


# Public suffixes, and the legal-entity words that a domain drops and a tracker
# row keeps (or the reverse). Stripping these is what lets `cgsinc.ca` reach a
# row reading "CGS Immersive".
_PUBLIC_SUFFIXES = {"com", "ca", "org", "net", "io", "ai", "co", "us", "uk", "info", "biz", "dev"}
_ENTITY_SUFFIXES = ("incorporated", "corporation", "holdings", "company", "group", "labs",
                    "inc", "corp", "ltd", "llc", "llp", "plc", "gmbh", "sa", "nv", "hq")
# Domain words that name a hosting product rather than the employer.
_HOSTING_WORDS = {"mail", "email", "smtp", "mailer", "send", "hire", "careers", "jobs",
                  "recruiting", "recruitment", "talent", "apply", "my", "www", "info"}


def _stem(word: str) -> str:
    """A company word with its legal-entity tail removed, if it has one."""
    for suffix in _ENTITY_SUFFIXES:
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _domain_tokens(domain: str) -> set[str]:
    """Words in a domain that could name the employer.

    `hire.lever.co` -> {"lever"}; `cgsinc.ca` -> {"cgsinc", "cgs"}.
    Both the raw word and its stem are kept, so a row that spells the entity
    suffix out still matches.
    """
    tokens: set[str] = set()
    for part in domain.split("."):
        if not part or part in _PUBLIC_SUFFIXES or part in _HOSTING_WORDS or len(part) < 3:
            continue
        tokens.add(part)
        stem = _stem(part)
        if len(stem) >= 3:
            tokens.add(stem)
    return tokens


def match_tracker(sender: str, entries: list[TrackerEntry]) -> TrackerEntry | None:
    """The tracker row whose employer name this sender's domain belongs to.

    Domain-first rather than display-name-first: a recruiter writes from the
    employer's domain even when the display name is only their own name. That
    is the case this was built for — `Gerandy Pineda <GPineda@cgsinc.ca>` had
    to reach a row reading "CGS Immersive", where neither string contains the
    other and only the stems agree.

    Matching is by prefix in either direction, and it errs toward matching: a
    false positive shows one extra message, a false negative loses an interview.
    """
    tokens = _domain_tokens(sender_domain(sender))
    if not tokens:
        return None
    for entry in entries:
        company = normalize(entry.company)
        if len(company) < 3:
            continue
        for token in tokens:
            if company.startswith(token) or token.startswith(company):
                return entry
    return None


def classify(
    *,
    message_id: str,
    thread_id: str,
    date: datetime | None,
    sender: str,
    subject: str,
    headers: dict[str, str],
    entries: list[TrackerEntry],
) -> HumanMessage | None:
    """A HumanMessage when this is a person writing about something live.

    Returns None for automated mail, and for human mail with no tracker match
    and nothing being arranged — a friend's note is not this check's business.
    """
    if is_automated(sender, headers):
        return None

    matched = match_tracker(sender, entries)
    arranging = bool(_ARRANGING.search(subject))
    if not matched and not arranging:
        return None

    if matched and arranging:
        reason = f"employer in tracker #{matched.number}, and the subject is arranging something"
    elif matched:
        reason = f"employer in tracker #{matched.number}"
    else:
        reason = "a person is trying to arrange something"

    return HumanMessage(
        message_id=message_id,
        thread_id=thread_id,
        date=date,
        sender=sender,
        subject=subject,
        reason=reason,
        tracker_row=matched.number if matched else None,
        tracker_company=matched.company if matched else "",
    )


def scan(
    *,
    since: str = "7d",
    max_results: int = 120,
    client=None,
    tracker: TrackerRepository | None = None,
) -> list[HumanMessage]:
    """Human mail worth a look, newest first.

    Fetches only headers, so it costs one metadata call per message and never
    pulls a body. `client` and `tracker` are injectable for tests.
    """
    from job_hunt.config.models import load_settings
    from job_hunt.services.email.gmail_client import GmailClient

    if client is None:
        settings = load_settings()
        client = GmailClient(
            token_path=settings.email_ingest.token_path,
            credentials_path=settings.email_ingest.credentials_path,
            auth_mode=settings.email_ingest.auth_mode,
        )
    entries = (tracker or TrackerRepository()).parse()

    service = client.service()
    listed = client.list_messages(f"newer_than:{since}", max_results=max_results)

    found: list[HumanMessage] = []
    for item in listed:
        message_id = item.get("id")
        if not message_id:
            continue
        raw = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=[
                    "From", "Subject", "Date",
                    "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted",
                ],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in raw.get("payload", {}).get("headers", [])}
        parsed = client.parse_message(raw)
        message = classify(
            message_id=message_id,
            thread_id=raw.get("threadId", message_id),
            date=parsed.date,
            sender=headers.get("From", ""),
            subject=headers.get("Subject", ""),
            headers=headers,
            entries=entries,
        )
        if message:
            found.append(message)

    found.sort(key=lambda m: m.sort_key, reverse=True)
    return found
