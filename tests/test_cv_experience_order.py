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


# --- first-pass screening signals (2026-09-30) ---------------------------------

check_screen_signals = render_cv.check_screen_signals

CLEAN = """## Professional Summary

Recent Master of Data Analytics graduate who builds **LLM applications** in Python.

## Core Skills

**Languages:** Python, SQL

## Experience

### Technical Director — Iqidao | Dec 2014 – May 2021

- Owned system architecture. AWS work was **featured as a case study**.

## Selected Projects

### LearnArken | 2026 – ongoing

- Hybrid retrieval reached **Recall@5 0.99** on an 82-query set.

## Education

### Bachelor of Computer Science and Technology — BUPT

**Certifications:** PMP – Project Management Professional (2014–2020, expired)
"""


def test_clean_layout_passes():
    assert check_screen_signals(CLEAN) == []


def test_early_career_block_is_rejected():
    assert check_screen_signals(CLEAN + "\n### Early Career (2005–2014)\n\nroles\n")


def test_pre_2014_year_is_rejected():
    dated = CLEAN.replace("— BUPT", "— BUPT | 2001 – 2005")
    assert any("2001" in p for p in check_screen_signals(dated))


def test_projects_above_experience_is_rejected():
    head, rest = CLEAN.split("## Experience")
    exp, proj_and_after = rest.split("## Selected Projects")
    proj, after = proj_and_after.split("## Education")
    swapped = head + "## Selected Projects" + proj + "## Experience" + exp + "## Education" + after
    assert any("Projects come before" in p for p in check_screen_signals(swapped))


def test_two_bold_spans_on_a_bullet_is_rejected():
    noisy = CLEAN.replace("reached **Recall@5 0.99**", "reached **Recall@5 0.99** on **82 queries**")
    assert any("bold spans" in p for p in check_screen_signals(noisy))


def test_skill_group_labels_are_not_counted_as_bold():
    assert check_screen_signals(CLEAN.replace("Python, SQL", "Python, SQL **Tools:** Git")) == []


def test_long_summary_is_rejected():
    long = CLEAN.replace("in Python.", "in Python. " + "word " * 60)
    assert any("summary" in p for p in check_screen_signals(long))


# --- plain language (2026-10-27) ------------------------------------------------

check_plain_language = render_cv.check_plain_language


def test_clean_cv_reads_plainly():
    assert check_plain_language(CLEAN) == []


def test_long_bullet_is_rejected():
    long = CLEAN.replace("Owned system architecture.", "Owned " + "word " * 30)
    assert any("words" in p for p in check_plain_language(long))


def test_semicolon_bullet_is_rejected():
    glued = CLEAN.replace("reached **Recall@5 0.99** on", "reached **Recall@5 0.99**; ran on")
    assert any("semicolon" in p for p in check_plain_language(glued))


def test_nested_parentheticals_are_rejected():
    noisy = CLEAN.replace("on an 82-query set", "on an 82-query set (synthetic) (toy scale)")
    assert any("parentheticals" in p for p in check_plain_language(noisy))


def test_colon_list_summary_is_rejected():
    listy = CLEAN.replace("in Python.", "in Python: pipelines, checks and dashboards.")
    assert any("summary" in p for p in check_plain_language(listy))


def test_bullet_word_boundary():
    bullet = "- Owned system architecture. AWS work was **featured as a case study**."
    at = CLEAN.replace(bullet, "- " + " ".join(["w"] * 30))
    over = CLEAN.replace(bullet, "- " + " ".join(["w"] * 31))
    assert not any("words" in p for p in check_plain_language(at))
    assert any("31 words" in p for p in check_plain_language(over))


def test_markdown_link_parens_are_not_counted():
    linked = CLEAN.replace("on an 82-query set", "on an 82-query set (synthetic), see [repo](https://x.io/a)")
    assert check_plain_language(linked) == []


def test_url_in_summary_is_not_a_colon_list():
    url = CLEAN.replace("in Python.", "in Python, see https://example.com.")
    assert check_plain_language(url) == []


def test_only_a_summary_heading_starts_the_summary():
    other = CLEAN.replace("## Core Skills", "## Project Profile\n\nSee: this.\n\n## Core Skills")
    assert check_plain_language(other) == []


def test_full_width_semicolon_and_long_cjk_bullet_are_rejected():
    zh = CLEAN.replace("Owned system architecture.", "负责系统架构；搭建团队")
    assert any("semicolon" in p for p in check_plain_language(zh))
    long_zh = CLEAN.replace("Owned system architecture.", "数" * 62)
    assert any("words" in p for p in check_plain_language(long_zh))


def test_legacy_layout_still_gets_the_plain_language_check():
    glued = CLEAN.replace("reached **Recall@5 0.99** on", "reached **Recall@5 0.99**; ran on")
    early = glued + "\n### Early Career (2005–2014)\n\nroles\n"
    problems = render_cv.validate(early, allow_legacy_layout=True)
    assert any("semicolon" in p for p in problems)
    assert not any("pre-2014" in p for p in problems)


def test_redteam_rubric_keeps_plain_out_of_the_verdict():
    from job_hunt.services import redteam
    rubric = next(v for v in vars(redteam).values() if isinstance(v, str) and "## 3. HR READ" in v)
    assert "PLAIN is a third label, neither BLOCK nor WARN" in rubric
    assert "may still be VERDICT: SEND" in rubric
