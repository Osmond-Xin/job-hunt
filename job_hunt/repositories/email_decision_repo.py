from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from job_hunt.repositories.jsonl_log import JsonlLog, MalformedLine


class EmailEventDecision(BaseModel):
    id: str = Field(default_factory=lambda: f"decision_{uuid.uuid4().hex}")
    event_id: str
    decision: Literal["approved", "ignored"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: str = ""


class EmailDecisionRepository:
    def __init__(self, path: Path = Path("data/email-event-decisions.jsonl")):
        self.path = path
        self._log = JsonlLog(path, EmailEventDecision)

    def append(self, decision: EmailEventDecision) -> None:
        self._log.append(decision)

    def malformed(self) -> list[MalformedLine]:
        """Lines the reader had to skip. Empty when the file is healthy."""
        return self._log.malformed()

    def list(self, limit: int = 100000) -> list[EmailEventDecision]:
        return self._log.list(limit)

    def decided_event_ids(self) -> set[str]:
        return {decision.event_id for decision in self.list()}
