"""The reverse-chronological invariant, enforced where it cannot be talked around.

Ten of the seventeen hand-written résumés in the 2026-09-17/18 batch listed their
dated roles by JD-relevance instead of by date, and the LLM red team reported one
of them as "reverse-chronological" without checking. The page budget never broke
across the same batch because a script refuses to render an overflowing CV; this
test guards the equivalent gate for ordering.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("render_cv", ROOT / "scripts" / "render_cv.py")
render_cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_cv)

check_experience_order = render_cv.check_experience_order


ORDERED = """## Experience

### Data & ML Engineer (Internship) — FindGrant | Jan 2026 – Mar 2026

- a bullet

### Software Engineer & Technical Consultant (Freelance) | Oct 2022 – Dec 2024

- a bullet

### Platform Product Manager — Bailongma | Aug 2021 – Sep 2022

- a bullet

### Technical Director — Iqidao | Dec 2014 – May 2021

- a bullet

### Early Career (2005–2014)

Some roles.

## Education
"""


def test_reverse_chronological_passes():
    assert check_experience_order(ORDERED) == []


def test_role_promoted_for_relevance_is_caught():
    """The actual defect: the most JD-relevant role hoisted to the top."""
    blocks = ORDERED.split("### ")
    reordered = blocks[0] + "### " + "### ".join([blocks[4], blocks[1], blocks[2], blocks[3], blocks[5]])
    problems = check_experience_order(reordered)
    assert problems, "a 2014 role above a 2026 role must be rejected"
    assert "Iqidao" in problems[0] and "FindGrant" in problems[0]


def test_early_career_block_stays_last_without_tripping_the_check():
    assert "Early Career" not in "".join(check_experience_order(ORDERED))


def test_selected_experience_heading_is_also_checked():
    """Two of the batch's résumés titled the section 'Selected Experience'."""
    swapped = ORDERED.replace("## Experience", "## Selected Experience")
    blocks = swapped.split("### ")
    reordered = blocks[0] + "### " + "### ".join([blocks[2], blocks[1], blocks[3], blocks[4], blocks[5]])
    assert check_experience_order(reordered)


def test_undated_heading_is_not_a_false_positive():
    assert check_experience_order("## Experience\n\n### Volunteer work\n\n- a bullet\n") == []


def test_every_checked_in_cv_variant_is_ordered():
    """profile/cv.md is the source the pipeline copies its order from."""
    for path in sorted(ROOT.joinpath("profile").rglob("cv*.md")):
        assert check_experience_order(path.read_text(encoding="utf-8")) == [], path
