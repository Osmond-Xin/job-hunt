from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JobMeta(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    remote: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    employment_type: str = ""
    seniority: str = ""
    url: str = ""
    ats: str = ""
    jd_hash: str = ""
    raw_text: str = ""
    requirements: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)


class ArchetypeResult(BaseModel):
    archetype: str = ""
    confidence: float = 0.0
    rationale: str = ""
    key_signals: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    target_roles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    min_salary: int | None = None
    years_experience: int | None = None
    open_to_remote: bool = True
    preferred_archetypes: list[str] = Field(default_factory=list)
    avoid_companies: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    # Mode-resolved narrative bridge phrase. Populated by ``_normalize_profile``
    # from the active narrative variant (``narrative.student`` or top-level
    # ``narrative``) so downstream consumers (cover_letter, scoring) do not
    # re-derive the mode each time.
    exit_narrative: str = ""
    # Mode-resolved work-authorization / availability statement (from
    # ``narrative.availability``). Rendered truthfully in full-mode cover
    # letters so recruiters see the PGWP timeline instead of screening out.
    availability: str = ""
    # One-line work-authorization statement (from ``narrative.work_auth_line``)
    # for the CV PDF header — the tailored-CV path strips cv.md's contact
    # block, so the resume would otherwise carry no work-authorization line.
    work_auth_line: str = ""
    # The mode this profile was loaded under. Allows downstream code that
    # already has the profile in hand to inspect mode without re-reading
    # profile.yml. Mirrors what ``current_mode()`` returned at load time.
    mode: Literal["student", "full"] = "full"
