from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class EmailEventDecision(BaseModel):
    id: str = Field(default_factory=lambda: f"decision_{uuid.uuid4().hex}")
    event_id: str
    decision: Literal["approved", "ignored"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: str = ""


class EmailDecisionRepository:
    def __init__(self, path: Path = Path("data/email-event-decisions.jsonl")):
        self.path = path

    def append(self, decision: EmailEventDecision) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(decision.model_dump_json() + "\n")

    def list(self, limit: int = 100000) -> list[EmailEventDecision]:
        if not self.path.exists():
            return []
        decisions: list[EmailEventDecision] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            decisions.append(EmailEventDecision.model_validate(json.loads(line)))
        return decisions[-limit:]

    def decided_event_ids(self) -> set[str]:
        return {decision.event_id for decision in self.list()}
