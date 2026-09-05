from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from html import unescape

from job_hunt.models.events import ApplicationEvent

# Prose fragments that show up when a regex grabs the wrong span of an email
# body instead of a company or role name. Substring match, case-insensitive.
# Shared with reconcile._safe_import_candidate so there is one definition of
# "this is garbage", not two that can drift apart.
BAD_FRAGMENTS: tuple[str, ...] = (
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
    "and for",
)

# ATS platforms, assessment vendors, and job-alert senders that show up as
# the "company" in a parsed email but are never themselves the employer.
GENERIC_COMPANIES: frozenset[str] = frozenset(
    {
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
        "adp",
        "codesignal",
        "jobalert",
        "flexhire",
        "recruitee-mailbox",
    }
)

# First word signals prose rather than a name: a lowercase connective/pronoun
# that a real company or role name would not open with ("your application,
# Yi Disclaimer...", "this time, we are not moving forward...").
_PROSE_LEAD_WORDS = {
    "and",
    "for",
    "the",
    "with",
    "to",
    "we",
    "our",
    "your",
    "this",
    "that",
    "who",
    "which",
    "an",
    "on",
    "at",
    "in",
    "of",
    "so",
    "but",
    "or",
}

# A comma followed by a lowercase article/pronoun/conjunction is a sentence
# continuing, not a name continuing ("RBC, a global leader in...",
# "Mastercard, our people open new doors...").
_PROSE_COMMA_RE = re.compile(
    r",\s+(a|an|the|and|but|so|we|our|your|which|who|that|this|it|there)\b",
    re.IGNORECASE,
)

# A sentence boundary sitting inside the value: a period followed by a
# capitalized word that commonly opens a sentence ("work with CIBC. This
# video was created..."). Deliberately narrow — real abbreviations like
# "St. John's" or "Inc." are not in this word list, so they survive.
_PROSE_SENTENCE_BREAK_RE = re.compile(
    r"\.\s+(This|We|Our|Your|Thank|Please|After|While|You|It|The|If|Should|I)\b"
)


def _looks_like_prose(value: str) -> bool:
    """True when `value` reads as a fragment of a sentence rather than a
    name: a lowercase connective leading the string, a comma followed by a
    lowercase connective, or a sentence boundary embedded in the middle."""
    stripped = value.strip()
    if not stripped:
        return False
    first_word = re.split(r"[\s,]+", stripped, maxsplit=1)[0]
    if first_word and first_word[0].islower() and first_word.lower() in _PROSE_LEAD_WORDS:
        return True
    if _PROSE_COMMA_RE.search(stripped):
        return True
    if _PROSE_SENTENCE_BREAK_RE.search(stripped):
        return True
    return False


def is_valid_company_role_field(value: str, *, is_company: bool = False) -> bool:
    """Gate applied to an extracted company or role candidate. Rejects a
    known-bad prose fragment, a known ATS/assessment vendor masquerading as
    the employer (company only), or a value that otherwise reads as prose.
    Used both by `extract_company_role` at parse time and by
    `reconcile._safe_import_candidate`, so both sites agree on what "garbage"
    means."""
    lowered = value.lower()
    if any(fragment in lowered for fragment in BAD_FRAGMENTS):
        return False
    if is_company and value.strip().lower() in GENERIC_COMPANIES:
        return False
    if _looks_like_prose(value):
        return False
    return True


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str
    thread_id: str | None
    sender: str
    subject: str
    snippet: str
    body: str
    date: datetime


def classify_email_event(parsed: ParsedEmail) -> ApplicationEvent:
    readable = clean_email_text(f"{parsed.subject}\n{parsed.snippet}\n{parsed.body[:6000]}")
    text = readable.lower()
    event_type = "unknown"
    confidence = 0.35
    evidence: list[str] = []

    has_application_context = bool(
        re.search(
            r"application|applied|applying|candidate|recruiter|recruiting|talent acquisition|hiring team|interview|your interest in|thank you for applying",
            text,
        )
    )

    patterns: list[tuple[str, str, float, bool]] = [
        ("offer", r"offer letter|employment offer|compensation package", 0.9, True),
        ("interview", r"complete your interview|schedule (?:an? )?(?:interview|call)|calendar invite|calendly|moving forward.*interview", 0.85, False),
        (
            "rejection",
            r"unfortunately.{0,220}(?:"
            r"decided not to proceed|not (?:able to )?(?:proceed|move forward|moving forward|selected|available)"
            r"|unable to (?:proceed|move forward|moving forward)"
            r"|won't (?:be )?(?:proceeding|moving forward|move forward)"
            r"|won’t (?:be )?(?:proceeding|moving forward|move forward)"
            r"|will not (?:be )?(?:proceeding|moving forward|move forward)"
            r")"
            r"|(?:we|our team|hiring team).{0,100}(?:won't|won’t|will not|are unable to|cannot).{0,100}(?:move forward|moving forward|proceed)"
            r"|decided not to proceed|no longer under consideration|unable to move forward|were not selected|have not been selected",
            0.85,
            True,
        ),
        ("assessment", r"assessment|coding challenge|take[- ]home|homework", 0.85, False),
        ("application_received", r"application received|received your application|thank you for applying|thanks for applying", 0.85, False),
        ("recruiter_reply", r"recruiter|talent acquisition|hiring team", 0.7, False),
    ]

    for candidate, pattern, candidate_confidence, requires_application_context in patterns:
        if requires_application_context and not has_application_context:
            continue
        match = re.search(pattern, text)
        if match:
            event_type = candidate
            confidence = candidate_confidence
            evidence.append(match.group(0))
            break

    company, role = extract_company_role(parsed)
    needs_review = confidence < 0.85 or not company or not role or event_type == "unknown"
    return ApplicationEvent(
        id=f"evt_{uuid.uuid4().hex}",
        source="gmail",
        source_message_id=parsed.message_id,
        source_thread_id=parsed.thread_id,
        event_type=event_type,  # type: ignore[arg-type]
        event_time=parsed.date,
        company=company,
        role=role,
        sender=parsed.sender,
        subject=parsed.subject,
        snippet=parsed.snippet,
        evidence=evidence,
        confidence=confidence,
        needs_review=needs_review,
    )


def extract_company_role(parsed: ParsedEmail) -> tuple[str | None, str | None]:
    sender_domain = parsed.sender.split("@")[-1].split(">")[0].strip().lower() if "@" in parsed.sender else ""
    company = None
    if sender_domain:
        root = sender_domain.split(".")[0]
        blocked_roots = {
            "e",
            "mail",
            "email",
            "notifications",
            "greenhouse",
            "lever",
            "ashbyhq",
            "workday",
            "linkedin",
            "noreply",
            "no-reply",
            "ats",
            "app",
            "careers",
            "candidates",
            "hire",
            "applytojob",
            "pinpoint",
            "myworkday",
            "successfactors",
            "dayforce",
            "gem",
        }
        if len(root) > 2 and root not in blocked_roots:
            company = root.title()
    subject = clean_email_text(parsed.subject)
    text = clean_email_text(f"{parsed.subject}\n{parsed.body[:8000]}")
    role = None

    paired_patterns = [
        r"Thank you for applying to the (?P<role>.+?) position at (?P<company>.+?)[\.\n]",
        r"Your Application With (?P<company>.+?) - (?P<role>.+?)(?:\s+(?:Hi|Hello)\s|$)",
        r"Thank you for applying to (?P<company>.+?)!\s*-\s*(?P<role>.+?)(?:\n|$)",
        r"Thank you for applying to (?P<company>.+?)!\s+.*?applying to the (?P<role>.+?) role at (?P=company)",
        r"Thank you for your interest in (?P<company>.+?)\..{0,260}?current (?P<role>.+?) opening",
        r"applying to the (?P<role>.+?) role at (?P<company>.+?)[!\.\n]",
        r"applying for the (?P<role>.+?) role here at (?P<company>.+?)[\.\n]",
        r"apply for the (?P<role>.+?) position here at (?P<company>.+?)[\.\n]",
        r"Thank you for your interest in (?P<company>.+?) and the (?P<role>.+?) position",
        r"Thank you for your interest in (?P<role>.+?) at (?P<company>.+?)[!\.\n]",
        r"Update Regarding Your (?P<company>.+?) (?P<role>.+?) Application",
        r"(?P<role>Applied AI Engineer) @ (?P<company>Forward Financing)",
        r"poste de (?P<role>.+?) chez (?P<company>.+?)[\.\n]",
        r"application from (?P<company>.+?)\n.*?applying to the (?P<role>.+?) position at (?P=company)",
    ]
    for pattern in paired_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            company = clean_extracted_phrase(match.group("company")) or company
            role = clean_extracted_phrase(match.group("role")) or role
            break

    company_patterns = [
        r"An update on your application from ([A-Z][A-Za-z0-9& .'-]+)",
        r"application (?:to|with) ([A-Z][A-Za-z0-9& .'-]+)",
        r"interest in ([A-Z][A-Za-z0-9& .'-]+)$",
        r"Thank you for applying to ([A-Z][A-Za-z0-9& .'-]+)!",
    ]
    if not company:
        for pattern in company_patterns:
            match = re.search(pattern, subject, flags=re.IGNORECASE)
            if match:
                extracted_company = clean_extracted_phrase(match.group(1))
                if extracted_company:
                    company = extracted_company
                    break

    role_patterns = [
        r"applying for [A-Z]\d+-\d+,\s*(.+?)\.",
        r"position of (.+?) \(ID:",
        r"interest in the (.+?) position",
        r"applying to (.+)",
        r"applied to (.+)",
        r"application for (.+)",
        r"application: (.+)",
        r"for the (.+?) role",
        r"for (.+?) at ",
    ]
    if not role:
        for pattern in role_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                role = clean_extracted_phrase(match.group(1))
                break
    if role and company and role.lower() == company.lower():
        role = None
    if company and not is_valid_company_role_field(company, is_company=True):
        company = None
    if role and not is_valid_company_role_field(role, is_company=False):
        role = None
    return company, role


def clean_extracted_phrase(value: str) -> str | None:
    cleaned = clean_email_text(value)
    cleaned = re.sub(r"^(to|with|at|for|the)\s+", "", cleaned.strip(" -|:!."), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(role|position|opening)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or len(cleaned) < 3 or cleaned.lower() in {"applying", "application", "this time"}:
        return None
    return cleaned[:120]


def clean_email_text(value: str) -> str:
    cleaned = unescape(value or "")
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def parsed_email_from_message(message: Message, message_id: str, thread_id: str | None = None) -> ParsedEmail:
    subject = message.get("subject", "")
    sender = message.get("from", "")
    body = message.get_payload(decode=True)
    body_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body or "")
    return ParsedEmail(
        message_id=message_id,
        thread_id=thread_id,
        sender=sender,
        subject=subject,
        snippet=body_text[:240],
        body=body_text,
        date=datetime.now(timezone.utc),
    )
