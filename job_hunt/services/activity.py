from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from job_hunt.config.models import ActivityConfig


ActivityLevel = Literal["debug", "info", "warning", "error"]


class ActivityEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex}")
    type: str
    level: ActivityLevel = "info"
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str
    application_id: int | None = None
    run_id: str | None = None
    # Top-level operator mode at emission time. Lets later funnel analysis
    # slice apply outcomes by student vs full without joining against
    # profile.yml history. See docs/design-notes.md §N.5.
    mode: Literal["student", "full"] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ActivityLogger:
    def __init__(self, config: ActivityConfig):
        self.config = config

    def emit(self, event: ActivityEvent) -> None:
        if self.config.sinks.local_log.enabled:
            self._write_local(event)
        if self._should_send_slack(event):
            self._send_slack(event)

    def _write_local(self, event: ActivityEvent) -> None:
        path = self.config.sinks.local_log.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def _should_send_slack(self, event: ActivityEvent) -> bool:
        slack = self.config.sinks.slack
        if not slack.enabled:
            return False
        if event.type not in slack.events and event.level not in {"warning", "error"}:
            return False
        levels = ["debug", "info", "warning", "error"]
        return levels.index(event.level) >= levels.index(slack.min_level)

    def _send_slack(self, event: ActivityEvent) -> None:
        webhook = resolve_secret_ref(self.config.sinks.slack.webhook_secret_ref)
        if not webhook:
            return
        text = f"[job-hunt] {event.summary}\nType: {event.type}\nLevel: {event.level}"
        try:
            response = httpx.post(webhook, json={"text": text}, timeout=10)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._write_local(
                ActivityEvent(
                    type="activity.slack_failed",
                    level="warning",
                    summary=f"Failed to send Slack activity event {event.id}",
                    payload={"event_type": event.type, "error": str(exc)},
                )
            )


def resolve_secret_ref(ref: str) -> str | None:
    if ref.startswith("env:"):
        return os.getenv(ref.removeprefix("env:")) or None
    return None


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().lower()
    if not text:
        return None
    units = {
        "m": "minutes",
        "min": "minutes",
        "mins": "minutes",
        "minute": "minutes",
        "minutes": "minutes",
        "h": "hours",
        "hr": "hours",
        "hrs": "hours",
        "hour": "hours",
        "hours": "hours",
        "d": "days",
        "day": "days",
        "days": "days",
    }
    match = __import__("re").match(r"^(\d+)\s*([a-z]+)$", text)
    if not match:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("since must look like 30m, 12h, 1d, or an ISO timestamp") from None
    amount = int(match.group(1))
    unit = units.get(match.group(2))
    if not unit:
        raise ValueError("since unit must be minutes, hours, or days")
    return datetime.now(timezone.utc) - timedelta(**{unit: amount})


def read_activity(path: Path, limit: int = 50, since: str | None = None) -> list[ActivityEvent]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    cutoff = parse_since(since)
    events: list[ActivityEvent] = []
    for line in lines:
        if not line.strip():
            continue
        event = ActivityEvent.model_validate(json.loads(line))
        if cutoff and event.ts < cutoff:
            continue
        events.append(event)
    return events[-limit:]
