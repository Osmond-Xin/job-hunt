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
