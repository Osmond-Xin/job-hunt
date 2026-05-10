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

def _normalize_profile(raw: dict) -> dict:
    """Accept both the flat profile schema and nested profile schema."""
    if not isinstance(raw.get("candidate"), dict):
        return raw

    candidate = raw.get("candidate") or {}
    target_roles = raw.get("target_roles") or {}
    location = raw.get("location") or {}
    compensation = raw.get("compensation") or {}

    primary_roles = target_roles.get("primary") or []
    secondary_roles = target_roles.get("secondary") or []
    archetypes = target_roles.get("archetypes") or []
    preferred_archetypes = [
        item.get("name", "")
        for item in archetypes
        if isinstance(item, dict) and item.get("name")
    ]

    min_salary = None
    minimum = str(compensation.get("minimum") or "")
    digits = "".join(ch for ch in minimum if ch.isdigit())
    if digits:
        min_salary = int(digits) * (1000 if int(digits) < 1000 else 1)

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
        "min_salary": min_salary,
        "years_experience": 20,
        "open_to_remote": True,
        "preferred_archetypes": preferred_archetypes,
        "skills": raw.get("narrative", {}).get("superpowers", []),
    }
