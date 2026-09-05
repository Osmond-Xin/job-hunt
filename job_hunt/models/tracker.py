from __future__ import annotations

import re

from pydantic import BaseModel


class TrackerEntry(BaseModel):
    number: int
    date: str
    company: str
    role: str
    score: str
    status: str
    pdf: str
    report: str
    notes: str = ""


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
