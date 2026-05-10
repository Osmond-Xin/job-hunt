from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class ApplicationEvent(BaseModel):
    id: str
    source: Literal["gmail", "manual", "system_apply", "scan"]
    source_message_id: str | None = None
    source_thread_id: str | None = None
    event_type: Literal[
        "application_submitted",
        "application_received",
        "recruiter_reply",
        "assessment",
        "interview",
        "offer",
        "rejection",
        "withdrawn",
        "unknown",
    ]
    event_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    company: str | None = None
    role: str | None = None
    job_url: str | None = None
    sender: str = ""
    subject: str = ""
    snippet: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    tracker_entry_id: int | None = None
    needs_review: bool = False
