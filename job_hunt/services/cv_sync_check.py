"""Pre-evaluate consistency check.

Returns ``CVSyncResult(warnings, errors)``. Callers (typically the
``evaluate`` CLI) abort on errors and surface warnings to stderr.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path


_REQUIRED_PROFILE_FIELDS = ("full_name", "email", "location")
_EXAMPLE_MARKERS = ('"Jane Smith"', "Jane Smith", "your.email@example.com")
_DIGEST_FRESH_DAYS = 30

# Pattern for hardcoded metrics that should be sourced from profile/cv.md / profile/article-digest.md
_METRIC_PATTERN = re.compile(
    r"\b\d{2,4}\+?\s*(hours?|%|evals?|layers?|tests?|fields?|bases?)\b",
    re.IGNORECASE,
)
_PROMPT_DIRS = ("prompts",)


@dataclass
class CVSyncResult:
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _check_cv(root: Path, result: CVSyncResult) -> None:
    found = root / "profile" / "cv.md"
    if not found.exists():
        result.errors.append(
            "profile/cv.md not found. Add your CV before evaluating offers."
        )
        return
    if len(found.read_text(encoding="utf-8").strip()) < 100:
        result.warnings.append(f"{found} seems too short — confirm it contains the full CV.")


def _check_profile(root: Path, result: CVSyncResult) -> None:
    profile_path = root / "profile" / "profile.yml"
    if not profile_path.exists():
        result.errors.append(
            "profile/profile.yml not found. Copy config/profile.example.yml to profile/profile.yml and fill it in."
        )
        return
    text = profile_path.read_text(encoding="utf-8")
    for field_name in _REQUIRED_PROFILE_FIELDS:
        if field_name not in text:
            result.warnings.append(f"{profile_path} missing field: {field_name}")
    for marker in _EXAMPLE_MARKERS:
        if marker in text:
            result.warnings.append(
                f"{profile_path} still contains example placeholder ({marker!r})."
            )
            break


def _check_prompts(root: Path, result: CVSyncResult) -> None:
    for sub in _PROMPT_DIRS:
        prompt_root = root / sub
        if not prompt_root.exists():
            continue
        for path in prompt_root.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if (
                    "NEVER hardcode" in line
                    or "NUNCA hardcode" in line
                    or line.lstrip().startswith("#")
                    or line.lstrip().startswith("<!--")
                ):
                    continue
                match = _METRIC_PATTERN.search(line)
                if match:
                    result.warnings.append(
                        f"{path.relative_to(root)}:{line_no} possible hardcoded metric "
                        f"{match.group(0)!r} — should it come from profile/cv.md or profile/article-digest.md?"
                    )


def _check_digest_freshness(root: Path, result: CVSyncResult) -> None:
    digest_path = root / "profile" / "article-digest.md"
    if not digest_path.exists():
        return
    age_days = (time.time() - digest_path.stat().st_mtime) / 86400
    if age_days > _DIGEST_FRESH_DAYS:
        result.warnings.append(
            f"{digest_path.relative_to(root)} is {int(age_days)} days old — "
            "consider refreshing if your projects have new metrics."
        )


def run(root: Path | None = None) -> CVSyncResult:
    """Run all checks against ``root`` (default: cwd)."""
    base = root or Path.cwd()
    result = CVSyncResult()
    _check_cv(base, result)
    _check_profile(base, result)
    _check_prompts(base, result)
    _check_digest_freshness(base, result)
    return result
