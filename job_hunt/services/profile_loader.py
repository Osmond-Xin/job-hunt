"""Load structured CV facts (work experience, education) for Workday auto-fill.

Reads ``profile/cv-experience.yml`` when present, then embedded defaults. The Workday filler expects strings for
each field (``start_month`` / ``start_year`` are used directly as keyboard input
on the split-spinbutton date widgets), so values are normalized to strings even
when the yaml writes them as integers or ISO ``YYYY-MM`` shortcuts.

Also exposes ``current_mode()`` — the single source of truth for the top-level
student-vs-full switch documented in docs/design-notes.md Section N. Every
subsystem that branches on mode reads through this helper; nobody else parses
``profile/profile.yml`` for the ``mode`` field directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

_CV_EXPERIENCE_PATH = Path("profile/cv-experience.yml")
_PROFILE_PATH = Path("profile/profile.yml")

Mode = Literal["student", "full"]
_DEFAULT_MODE: Mode = "full"
_VALID_MODES: tuple[Mode, ...] = ("student", "full")


def current_mode(profile_path: Path | None = None) -> Mode:
    """Return the operator's current mode. Defaults to ``"full"`` when missing.

    The mode lives at the top level of ``profile/profile.yml`` as a plain
    string — see docs/design-notes.md Section N.2. Anything other than
    ``"student"`` or ``"full"`` (including missing file, malformed yaml, or
    typo) falls back to ``"full"`` so the system stays in its historical
    behaviour rather than silently switching to student.
    """
    path = profile_path if profile_path is not None else _PROFILE_PATH
    if not path.exists():
        return _DEFAULT_MODE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return _DEFAULT_MODE
    if not isinstance(raw, dict):
        return _DEFAULT_MODE
    value = raw.get("mode")
    if isinstance(value, str) and value.strip().lower() in _VALID_MODES:
        return value.strip().lower()  # type: ignore[return-value]
    return _DEFAULT_MODE


# Generic placeholder defaults. Real candidate data should come from
# profile/cv-experience.yml — see config/profile.example.yml and the project
# README for how to populate it.
_DEFAULT_EXPERIENCE: list[dict[str, str]] = [
    {
        "title": "Example Job Title",
        "company": "Example Company",
        "location": "City, Region, Country",
        "start_year": "",
        "start_month": "",
        "end_year": "",
        "end_month": "",
        "description": "Describe your role and achievements in profile/cv-experience.yml.",
    },
]


_DEFAULT_EDUCATION: list[dict[str, str]] = [
    {
        "school": "Example University",
        "degree": "Example Degree",
        "field": "Example Field",
        "gpa": "",  # filled from values["gpa_4_scale"] at call time when blank
        "start_year": "",
        "start_month": "",
        "end_year": "",
        "end_month": "",
    },
]


def _split_year_month(value: Any) -> tuple[str, str]:
    """Accept ``"2026-01"`` / ``"2026/1"`` / ``date`` / ``{year, month}``; return (year, month) strings."""
    if value is None:
        return "", ""
    if isinstance(value, dict):
        return str(value.get("year", "")), str(value.get("month", ""))
    text = str(value).strip()
    if not text:
        return "", ""
    for sep in ("-", "/", "."):
        if sep in text:
            year, _, month = text.partition(sep)
            return year.strip(), month.strip().lstrip("0") or "0"
    return text, ""


def _normalize_experience_entry(raw: dict[str, Any]) -> dict[str, str]:
    if "start" in raw or "end" in raw:
        start_year, start_month = _split_year_month(raw.get("start"))
        end_year, end_month = _split_year_month(raw.get("end"))
    else:
        start_year = str(raw.get("start_year", ""))
        start_month = str(raw.get("start_month", ""))
        end_year = str(raw.get("end_year", ""))
        end_month = str(raw.get("end_month", ""))
    return {
        "title": str(raw.get("title", "")),
        "company": str(raw.get("company", "")),
        "location": str(raw.get("location", "")),
        "start_year": start_year,
        "start_month": start_month,
        "end_year": end_year,
        "end_month": end_month,
        "description": str(raw.get("description", "")),
    }


def _normalize_education_entry(raw: dict[str, Any]) -> dict[str, str]:
    if "start" in raw or "end" in raw:
        start_year, start_month = _split_year_month(raw.get("start"))
        end_year, end_month = _split_year_month(raw.get("end"))
    else:
        start_year = str(raw.get("start_year", ""))
        start_month = str(raw.get("start_month", ""))
        end_year = str(raw.get("end_year", ""))
        end_month = str(raw.get("end_month", ""))
    return {
        "school": str(raw.get("school", "")),
        "degree": str(raw.get("degree", "")),
        "field": str(raw.get("field", "")),
        "gpa": str(raw.get("gpa", "") or ""),
        "start_year": start_year,
        "start_month": start_month,
        "end_year": end_year,
        "end_month": end_month,
    }


def _load_raw() -> dict[str, Any]:
    if not _CV_EXPERIENCE_PATH.exists():
        return {}
    try:
        return yaml.safe_load(_CV_EXPERIENCE_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def workday_experience_entries() -> list[dict[str, str]]:
    raw = _load_raw()
    items = raw.get("experience") if isinstance(raw, dict) else None
    if not items:
        return [dict(entry) for entry in _DEFAULT_EXPERIENCE]
    return [_normalize_experience_entry(entry) for entry in items if isinstance(entry, dict)]


def workday_education_entries(values: dict[str, str]) -> list[dict[str, str]]:
    raw = _load_raw()
    items = raw.get("education") if isinstance(raw, dict) else None
    entries = (
        [_normalize_education_entry(entry) for entry in items if isinstance(entry, dict)]
        if items
        else [dict(entry) for entry in _DEFAULT_EDUCATION]
    )
    fallback_gpa = values.get("gpa_4_scale", "") if values else ""
    if entries and not entries[0].get("gpa") and fallback_gpa:
        entries[0]["gpa"] = fallback_gpa
    return entries
