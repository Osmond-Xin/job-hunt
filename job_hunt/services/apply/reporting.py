"""Reading the evaluation report a tracker row points at.

Split out of ``cli/apply.py`` (see docs/apply-seam-plan.md, Phase 1). These
functions never touch a browser: they read a report off disk, pull the section
the apply flow needs, and decide whether the score clears the ethical-use gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from job_hunt.repositories.tracker_repo import TrackerRepository
from job_hunt.services.employer_match import MATCH_THRESHOLD, EmployerMatcher


# Tracks the Ethical Use threshold in `prompts/shared.md`, lowered 4.0 → 3.0 on
# 2026-08-16. Leaving it at 4.0 would have aborted the apply flow for every role
# the scorer now recommends in the 3.0–4.0 band — Whitby at 3.73 among them.
_LOW_SCORE_GATE_THRESHOLD = 3.0


def _resolve_report_path(report_ref: str) -> Path | None:
    if not report_ref:
        return None
    match = re.search(r"\((reports/[^)]+)\)", report_ref)
    raw = match.group(1) if match else report_ref.strip()
    raw = raw.strip("[]")
    if raw.startswith("manual:"):
        return None
    path = Path(raw)
    if path.exists():
        return path
    if not raw.startswith("reports/") and raw.endswith(".md"):
        path = Path("reports") / raw
        if path.exists():
            return path
    return None


def _extract_application_section(report_text: str) -> str:
    headings = [
        r"section g",
        r"application answers",
        r"application framing",
        r"draft answers",
        r"key talking points",
        r"application requirements",
    ]
    pattern = re.compile(rf"^##+\s+.*({'|'.join(headings)}).*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(report_text)
    if not match:
        return ""
    start = match.start()
    next_heading = re.search(r"^##\s+", report_text[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(report_text)
    return report_text[start:end].strip()[:6000]


def _extract_report_recommendation(report_text: str) -> str:
    match = re.search(r"Recommendation\*\*:\s*([A-Za-z]+)", report_text)
    return match.group(1).upper() if match else ""


def _load_apply_report_context(
    *,
    tracker: TrackerRepository,
    tracker_entry,
    company: str | None,
    role: str | None,
) -> dict | None:
    entry = tracker_entry
    score = 1.0 if entry else 0.0
    if entry is None:
        entry, score = EmployerMatcher(tracker.parse()).raw_match(company=company, role=role)
    if not entry or score < MATCH_THRESHOLD:
        return None

    report_path = _resolve_report_path(entry.report)
    if not report_path or not report_path.exists():
        return {
            "tracker_id": entry.number,
            "company": entry.company,
            "role": entry.role,
            "score": entry.score,
            "status": entry.status,
            "path": "",
            "application_section": "",
        }
    text = report_path.read_text(encoding="utf-8")
    return {
        "tracker_id": entry.number,
        "company": entry.company,
        "role": entry.role,
        "score": entry.score,
        "status": entry.status,
        "path": str(report_path),
        "recommendation": _extract_report_recommendation(text),
        "application_section": _extract_application_section(text),
    }


def _report_fit_warnings(report_context: dict | None) -> list[str]:
    if not report_context:
        return []
    warnings = []
    score_text = report_context.get("score") or ""
    recommendation = (report_context.get("recommendation") or "").upper()
    score_match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5", score_text)
    if recommendation == "SKIP":
        warnings.append("Matched report recommendation is SKIP; confirm with the user before applying.")
    if score_match and float(score_match.group(1)) < 3.0:
        warnings.append(f"Matched report score is low ({score_text}); treat this as a review blocker.")
    return warnings


@dataclass(frozen=True)
class LowScoreVerdict:
    """What the ethical-use gate decided, and nothing about how to say it.

    ``allowed`` is the only field the caller must act on. ``score`` is present
    whenever one was parseable, so a message can name it.
    """

    allowed: bool
    overridden: bool = False
    score: float | None = None
    threshold: float = _LOW_SCORE_GATE_THRESHOLD


def low_score_verdict(report_context: dict | None, *, override: bool) -> LowScoreVerdict:
    """Decide whether a below-threshold tracker score may proceed to apply.

    Per `prompts/shared.md` Ethical Use rules, applying to a low-score role costs
    recruiter attention. "Low" means a blocker the candidate cannot satisfy, not
    an imperfect match. The gate fires only when (a) there is a tracker match
    with a parseable score and (b) that score is below the threshold. When no
    score is available (manual cases, fresh tracker rows, "N/A" / "DUP"), it
    stays silent rather than blocking legitimate manual workflows.

    Returns a verdict rather than printing and raising, so the decision is
    testable without capturing stdout and the wording lives in `cli/` where the
    rest of the user-facing text does. See docs/apply-seam-plan.md §3.4.
    """
    if not report_context:
        return LowScoreVerdict(allowed=True)
    score_str = (report_context.get("score") or "").strip()
    match = re.match(r"^([\d.]+)/5", score_str)
    if not match:
        return LowScoreVerdict(allowed=True)
    score = float(match.group(1))
    if score >= _LOW_SCORE_GATE_THRESHOLD:
        return LowScoreVerdict(allowed=True, score=score)
    if override:
        return LowScoreVerdict(allowed=True, overridden=True, score=score)
    return LowScoreVerdict(allowed=False, score=score)
