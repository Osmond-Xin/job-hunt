from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PdfContent(BaseModel):
    """Per-company content injected into the shared CV/cover-letter template."""

    summary_angle: str = ""
    top_bullets: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    cover_letter_body: str = ""


class DimensionScore(BaseModel):
    dimension: str
    score: float
    weight: float
    rationale: str


class EvaluationScores(BaseModel):
    dimensions: list[DimensionScore] = Field(default_factory=list)
    weighted_total: float = 0.0
    out_of: float = 5.0
    recommendation: Literal["apply", "maybe", "skip"] = "skip"
    recommendation_rationale: str = ""
    generate_pdf: bool = False
    pdf_content: PdfContent = Field(default_factory=PdfContent)
    gaps: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
