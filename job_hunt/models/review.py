from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReviewItem(BaseModel):
    id: str = Field(default_factory=lambda: f"rev_{uuid.uuid4().hex}")
    type: Literal[
        "email_match_low_confidence",
        "duplicate_candidate",
        "status_update_conflict",
        "form_field_uncertain",
        "low_score_override",
        "secret_required",
        "cookie_expired",
        "artifact_quality_warning",
    ]
    status: Literal["open", "approved", "rejected", "resolved"] = "open"
    priority: Literal["low", "normal", "high"] = "normal"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)

