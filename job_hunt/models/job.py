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
