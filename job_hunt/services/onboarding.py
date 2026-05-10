"""Conversational onboarding helpers for `job-hunt init --guided`.

Five open-ended questions whose answers populate `profile/profile.yml::narrative`
and seed the evaluator's adaptive framing. Designed to be called AFTER the
basic file scaffolding (`profile/cv.md`, `profile/profile.yml`, `data/applications.md`)
already exists.

Pure functions (no Typer dependency) so they are easy to unit-test with
``input_provider`` injection. The CLI layer wires ``typer.prompt`` /
``typer.confirm`` in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml


@dataclass
class NarrativeAnswers:
    superpower: str = ""
    energy: str = ""
    drains: str = ""
    deal_breakers: str = ""
    best_achievement: str = ""
    proof_links: str = ""
    exit_story: str = ""

    def is_meaningful(self) -> bool:
        """True if the user provided non-trivial answers (not just defaults / blank)."""
        joined = " ".join(
            [
                self.superpower,
                self.energy,
                self.drains,
                self.deal_breakers,
                self.best_achievement,
                self.proof_links,
                self.exit_story,
            ]
        ).strip()
        return len(joined) > 30


# Question bank for the guided profile narrative.
# Each tuple is (key, prompt, default).
_QUESTIONS: list[tuple[str, str, str]] = [
    (
        "superpower",
        "What's your 'superpower' — the thing other candidates with similar resumes "
        "usually don't have?",
        "",
    ),
    (
        "energy",
        "What kind of work *excites* you? (e.g. 0-to-1 prototyping, scaling reliable "
        "systems, mentoring teams, deep research)",
        "",
    ),
    (
        "drains",
        "What kind of work *drains* you? (Optional — useful for filtering bad-fit roles.)",
        "",
    ),
    (
        "deal_breakers",
        "Any deal-breakers? (e.g. no on-site, no startups <20 people, no Java shops, "
        "must allow contracting)",
        "",
    ),
    (
        "best_achievement",
        "Your best professional achievement — the one you'd lead with in an interview. "
        "One sentence.",
        "",
    ),
    (
        "proof_links",
        "Any portfolio / articles / case studies / public projects? (Comma-separated URLs, optional.)",
        "",
    ),
    (
        "exit_story",
        "Exit story — one sentence on the bridge from your past role to your next "
        "(used to frame summaries / cover letters). Optional.",
        "",
    ),
]


def collect_narrative(
    input_provider: Callable[[str, str], str],
) -> NarrativeAnswers:
    """Walk through the question bank with ``input_provider(prompt, default) -> answer``.

    The CLI wires ``typer.prompt`` here. Tests inject a deterministic mock.
    """
    answers = NarrativeAnswers()
    for key, prompt, default in _QUESTIONS:
        raw = input_provider(prompt, default)
        setattr(answers, key, (raw or "").strip())
    return answers


def merge_into_profile(
    profile_path: Path,
    answers: NarrativeAnswers,
) -> bool:
    """Merge ``answers`` into ``profile_path``'s ``narrative`` block.

    Returns True if the file was written. False when nothing meaningful was
    captured (don't pollute profile.yml with empty fields).
    """
    if not answers.is_meaningful():
        return False
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if profile_path.exists():
        loaded = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    narrative = dict(data.get("narrative") or {})
    for key in (
        "superpower",
        "energy",
        "drains",
        "deal_breakers",
        "best_achievement",
        "proof_links",
        "exit_story",
    ):
        value = getattr(answers, key)
        if value:
            narrative[key] = value
    data["narrative"] = narrative
    profile_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return True


def has_narrative(profile_path: Path) -> bool:
    """True when profile.yml already has a meaningful ``narrative`` block."""
    if not profile_path.exists():
        return False
    loaded = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return False
    narrative = loaded.get("narrative") or {}
    if not isinstance(narrative, dict):
        return False
    return any(str(v).strip() for v in narrative.values())


def final_message(
    *,
    cv_present: bool,
    profile_present: bool,
    tracker_present: bool,
) -> str:
    """Compose the closing message printed at the end of guided init."""
    lines = ["[bold green]You're all set.[/bold green]", ""]
    lines.append("Status:")
    lines.append(f"  profile/cv.md ................ {'✓' if cv_present else '✗ (run import-resume)'}")
    lines.append(f"  profile/profile.yml .......... {'✓' if profile_present else '✗'}")
    lines.append(f"  data/applications.md ......... {'✓' if tracker_present else '✗'}")
    lines.append("")
    lines.append("Next:")
    lines.append("  1. Paste a job URL: job-hunt evaluate '<url>'")
    lines.append("  2. Or scan portals:  job-hunt scan --save")
    lines.append("  3. Configure email reconcile (optional): job-hunt email poll --live")
    lines.append("")
    lines.append(
        "Tip: schedule a recurring scan via /loop or /schedule when you're ready."
    )
    return "\n".join(lines)


# Convenience entry point for the CLI — keeps Typer out of this module.
def run_guided_questions(
    profile_path: Path,
    input_provider: Callable[[str, str], str],
) -> bool:
    """Top-level helper: collect answers + persist to profile.yml.

    Returns True when narrative was written (i.e. user provided real input).
    """
    answers = collect_narrative(input_provider)
    return merge_into_profile(profile_path, answers)
