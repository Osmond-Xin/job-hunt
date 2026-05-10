from __future__ import annotations

import os
import uuid
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from job_hunt.config.models import Settings


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    enabled: bool
    project: str


class TraceManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def enabled(self) -> bool:
        configured = self.settings.observability.langsmith.enabled
        env = os.getenv("LANGSMITH_TRACING")
        if self.settings.observability.langsmith.respect_env and env is not None:
            return env.lower() in {"1", "true", "yes", "on"}
        return configured

    def status(self) -> TraceContext:
        return TraceContext(
            trace_id=f"local:{uuid.uuid4().hex}",
            enabled=self.enabled(),
            project=self.settings.observability.langsmith.project,
        )

    @contextmanager
    def trace(self, name: str) -> Iterator[TraceContext]:
        _ = name
        yield self.status()


def write_usage_ledger(settings: Settings, record: dict) -> None:
    if not settings.observability.local_ledger.enabled:
        return
    path = Path(settings.paths.data_dir) / "usage-ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def langsmith_enabled(settings: Settings) -> bool:
    return TraceManager(settings).enabled()


def get_langsmith_traceable(settings: Settings):
    if not langsmith_enabled(settings):
        return None
    from langsmith import traceable

    return traceable
