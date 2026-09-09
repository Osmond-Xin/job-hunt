"""Where an apply session keeps its artifacts.

Split out of ``cli/apply.py`` (see docs/apply-seam-plan.md, Phase 1).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _apply_artifact_dir(company: str | None, role: str | None) -> Path:
    """Return a per-application artifact directory under artifacts/apply/."""
    import re as _re
    today = datetime.now().date().isoformat()
    def _slug(s: str, max_len: int) -> str:
        return _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:max_len]
    parts = [today]
    if company:
        parts.append(_slug(company, 25))
    if role:
        parts.append(_slug(" ".join(role.split()[:4]), 30))
    return Path("artifacts/apply") / "-".join(parts)


def application_id(company: str | None, role: str | None) -> str:
    """A stable name for one application, independent of when it was worked on.

    The artifact directory is a *display* name: it carries today's date, a
    company truncated to 25 characters and the first four words of the role.
    Two things follow, and a 2026-09-09 review found both. A retry the next day
    lands in a different directory, so anything keyed on the directory forgets
    what yesterday's run did. And two roles that agree on their first four words
    -- "Senior Software Engineer, Platform A" and "... Platform B" -- collide.

    This is what state that must survive a day, and must not be shared between
    two different jobs, is keyed on instead. No date, no truncation.
    """
    import re as _re

    def _norm(value: str | None) -> str:
        return _re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")

    return f"{_norm(company)}::{_norm(role)}"
