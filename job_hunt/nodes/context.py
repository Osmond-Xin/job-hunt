"""load_context node — reads candidate context from stable local files."""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml
from langchain_core.runnables import RunnableConfig

from job_hunt.models.job import CandidateProfile
from job_hunt.models.state import JobHuntState

_CV_PATH = Path("profile/cv.md")
_PROFILE_PATH = Path("profile/profile.yml")
_DIGEST_PATH = Path("profile/article-digest.md")


async def load_context(state: JobHuntState, config: RunnableConfig) -> dict:
    errors: list[str] = []
    cv_path = _CV_PATH if _CV_PATH.exists() else None
    profile_path = _PROFILE_PATH if _PROFILE_PATH.exists() else None
    digest_path = _DIGEST_PATH if _DIGEST_PATH.exists() else None

    cv = cv_path.read_text(encoding="utf-8") if cv_path else ""
    article_digest = digest_path.read_text(encoding="utf-8") if digest_path else None
    if not cv_path:
        errors.append("Candidate CV context not found; expected profile/cv.md.")

    profile = CandidateProfile()
    if profile_path:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        profile = CandidateProfile.model_validate(_normalize_profile(raw))
    else:
        errors.append("Candidate profile not found; expected profile/profile.yml.")

    run_id = state.get("run_id") or uuid.uuid4().hex
    return {
        "run_id": run_id,
        "cv": cv,
        "profile": profile,
        "article_digest": article_digest,
        "errors": errors,
    }

def _resolve_mode(raw: dict) -> str:
    """Read top-level ``mode`` from the raw profile dict; default ``"full"``."""
    value = raw.get("mode")
    if isinstance(value, str) and value.strip().lower() in ("student", "full"):
        return value.strip().lower()
    return "full"


def _select_narrative(raw_narrative: dict, mode: str) -> dict:
    """Pick the mode-active narrative block.

    - ``mode == "student"`` and ``narrative.student`` present → use that block.
    - Otherwise fall back to the top-level ``narrative.*`` fields, which act
      as the full-mode narrative by convention (see profile/profile.yml).
    """
    if not isinstance(raw_narrative, dict):
        return {}
    if mode == "student":
        student_block = raw_narrative.get("student")
        if isinstance(student_block, dict):
            return student_block
    return raw_narrative


def _resolve_min_salary(compensation: dict, mode: str) -> int | None:
    """Pick the mode-appropriate compensation minimum.

    Annual values like ``"CAD 80K"`` parse as 80000. Hourly rates like
    ``"CAD 22/hr"`` parse as the integer rate (22). The legacy heuristic
    ``value * 1000 if value < 1000 else value`` is only applied when the
    surrounding string does not look like an hourly rate.
    """
    candidates: list[str] = []
    if mode == "student":
        candidates.append(str(compensation.get("student_minimum") or ""))
    candidates.append(str(compensation.get("minimum") or ""))
    for raw_value in candidates:
        digits = "".join(ch for ch in raw_value if ch.isdigit())
        if not digits:
            continue
        value = int(digits)
        if _looks_hourly(raw_value):
            return value
        return value * (1000 if value < 1000 else 1)
    return None


def _looks_hourly(raw: str) -> bool:
    lower = raw.lower()
    return any(token in lower for token in ("/hr", "/hour", " hr", " hour", "hourly"))


def _normalize_profile(raw: dict) -> dict:
    """Accept both the flat profile schema and nested profile schema.

    Mode-aware: when ``mode: student`` is set at the top level, the narrative
    bridge (``exit_narrative``) and the default skills list are sourced from
    ``narrative.student`` rather than the top-level narrative block. See
    docs/design-notes.md §N.3.
    """
    if not isinstance(raw.get("candidate"), dict):
        return raw

    candidate = raw.get("candidate") or {}
    target_roles = raw.get("target_roles") or {}
    location = raw.get("location") or {}
    compensation = raw.get("compensation") or {}
    mode = _resolve_mode(raw)
    narrative = _select_narrative(raw.get("narrative") or {}, mode)

    primary_roles = target_roles.get("primary") or []
    secondary_roles = target_roles.get("secondary") or []
    archetypes = target_roles.get("archetypes") or []
    # Filter archetypes by mode: missing eligibility tag defaults to "full"
    # so legacy entries continue to behave the same way.
    preferred_archetypes = [
        item.get("name", "")
        for item in archetypes
        if isinstance(item, dict)
        and item.get("name")
        and str(item.get("eligibility") or "full").strip().lower() == mode
    ]
    # Backstop: if mode-filtered list is empty (e.g. profile.yml has no
    # eligibility tags yet), fall back to the unfiltered list so the previous
    # behaviour is preserved during incremental migration.
    if not preferred_archetypes:
        preferred_archetypes = [
            item.get("name", "")
            for item in archetypes
            if isinstance(item, dict) and item.get("name")
        ]

    target_locations = [
        item
        for item in [
            location.get("city"),
            location.get("province"),
            location.get("country"),
            location.get("open_to_relocation"),
        ]
        if item
    ]

    return {
        "name": candidate.get("full_name", ""),
        "email": candidate.get("email", ""),
        "phone": candidate.get("phone", ""),
        "location": candidate.get("location", ""),
        "linkedin": candidate.get("linkedin", ""),
        "github": candidate.get("github", ""),
        "website": candidate.get("portfolio_url", ""),
        "target_roles": [*primary_roles, *secondary_roles],
        "target_locations": target_locations,
        "min_salary": _resolve_min_salary(compensation, mode),
        "years_experience": 20,
        "open_to_remote": True,
        "preferred_archetypes": preferred_archetypes,
        "skills": narrative.get("superpowers", []),
        "exit_narrative": narrative.get("exit_story", "") or "",
        "availability": narrative.get("availability", "") or "",
        "mode": mode,
    }
