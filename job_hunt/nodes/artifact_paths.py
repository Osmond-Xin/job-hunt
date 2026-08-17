"""Shared naming for a run's artifacts.

The report file and the ``output/`` directory share one stem —
``2026-07-28-arken-ai-engineer-13c0e173`` — so an evaluation's report and its
PDFs are findable by date or company and pair up by name. The trailing run-id
fragment keeps two evaluations of the same job on the same day apart.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from job_hunt.models.state import JobHuntState

_OUTPUT_DIR = Path("output")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30]


def run_stem(state: JobHuntState) -> str:
    """``2026-07-28-arken-ai-engineer-13c0e173`` — date, employer, role, run id.

    The employer segment is the one people navigate by, so an empty company
    must not collapse into a bare ``--``: three of 2026-08-17's runs came out
    as ``2026-08-17--ai-solutions-engineer-adzuna-c-…``, which names the job
    board and not the employer. Fall back to a word that says what is missing.
    """
    jd_meta = state.get("jd_meta")
    company = slug(jd_meta.company if jd_meta else "") or "unknown-employer"
    role = slug(jd_meta.title if jd_meta else "") or "unknown-role"
    run_id = state.get("run_id", "unknown")
    date = datetime.date.today().isoformat()
    return f"{date}-{company}-{role}-{run_id[:8]}"


def run_output_dir(state: JobHuntState) -> Path:
    return _OUTPUT_DIR / run_stem(state)
