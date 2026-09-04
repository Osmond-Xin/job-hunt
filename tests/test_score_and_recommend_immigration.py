"""services/triage.py had geography scored twice — once in triage's own table
(removed, see "Rank on the job, and stop ranking on the map"), and again here,
more aggressively, in the evaluate scorer's prompt. This file pins that the
second copy is gone: an `immigration_context` (from
`job_hunt.services.immigration.immigration_context`, backed by
`profile.yml::immigration_priority`) may be *stated* in the prompt as a fact,
but must never inflate a dimension, forgive a shortfall, or move the
recommendation threshold.

It also pins the "Score: 3.88 / Weighted total: 3.38" divergence found on a
live 2026-09-03 run: the reported score and the weighted total must always be
the same number, computed the same way.

No real LLM is called anywhere in this file — only prompt rendering
(job_hunt.services.prompts.render) and JSON parsing
(job_hunt.nodes.evaluate._parse_scores) against fake model output.
"""

from __future__ import annotations

import json

from job_hunt.nodes.evaluate import _parse_scores
from job_hunt.services.prompts import render

BLOCKS = {
    "role_summary": "",
    "cv_match": "",
    "level_strategy": "",
    "comp_research": "",
    "personalization": "",
    "archetype": "",
}

# Phrases that used to compound a priority-region location into the score:
# a Company-fit bonus, a forgiven Domain-fit shortfall, and a 0.5-lower
# threshold. None of them may appear in the rendered prompt any more,
# in or out of a priority region.
_REMOVED_ADJUSTMENT_PHRASES = [
    "strong positive when scoring Company fit",
    "Accept a weaker Domain fit than usual",
    "compensates for up to one point of domain-fit shortfall",
    "thresholds 0.5 lower",
]


def _scoring_prompt(*, immigration_context: str, mode: str = "full") -> str:
    return render(
        "evaluate/score_and_recommend.md",
        evaluation_blocks=BLOCKS,
        mode=mode,
        cv="",
        article_digest="",
        jd_text="",
        immigration_context=immigration_context,
        generate_cover_letter=False,
    )


def _strip_immigration_section(prompt: str) -> str:
    """Drop the (optional) immigration section so the remainder can be diffed.

    Whichever branch of the `{% if immigration_context %}` fired, normalize
    to exactly one blank line before "## Evaluation inputs" — the Jinja
    whitespace control around an empty vs. non-empty `{% if %}` block leaves
    a different blank-line count behind, which is not a content difference
    worth pinning.
    """
    marker = "## Immigration-pathway context"
    if marker in prompt:
        start = prompt.index(marker)
        end = prompt.index("## Evaluation inputs")
        prompt = prompt[:start].rstrip("\n") + "\n\n" + prompt[end:]
    else:
        idx = prompt.index("## Evaluation inputs")
        prompt = prompt[:idx].rstrip("\n") + "\n\n" + prompt[idx:]
    return prompt


def test_no_scoring_adjustment_phrases_survive_in_priority_region() -> None:
    ctx = (
        "JD location `Toronto, ON` matches the operator's immigration-priority "
        "regions: ontario, toronto."
    )
    prompt = _scoring_prompt(immigration_context=ctx)
    for phrase in _REMOVED_ADJUSTMENT_PHRASES:
        assert phrase not in prompt, f"scoring adjustment phrase resurfaced: {phrase!r}"


def test_immigration_context_is_stated_as_informational_only() -> None:
    ctx = "JD location `Toronto, ON` matches ... ontario, toronto."
    prompt = _scoring_prompt(immigration_context=ctx)
    assert ctx in prompt
    assert "no effect on scoring" in prompt
    assert "does not raise a dimension score" in prompt
    assert "does not move the threshold" in prompt


def test_priority_region_and_non_priority_region_prompts_are_otherwise_identical() -> None:
    """Same dimension tables, same JSON schema, same thresholds either way —
    the only difference a priority-region JD introduces is the informational
    immigration section itself."""
    with_ctx = _strip_immigration_section(
        _scoring_prompt(immigration_context="JD location matches a priority region.")
    )
    without_ctx = _strip_immigration_section(_scoring_prompt(immigration_context=""))
    assert with_ctx == without_ctx


def test_weighted_total_formula_is_stated_explicitly() -> None:
    prompt = _scoring_prompt(immigration_context="")
    assert "sum(dimension.score * dimension.weight)" in prompt
    assert "Never adjust it up or down for" in prompt


def test_shared_prompt_no_longer_grants_a_location_scoring_bonus() -> None:
    shared = render(
        "evaluate/score_and_recommend.md",
        evaluation_blocks=BLOCKS,
        mode="full",
        cv="",
        article_digest="",
        jd_text="",
        immigration_context="",
        generate_cover_letter=False,
    )
    assert "priority immigration region is a **positive**" not in shared
    assert "not a scoring positive" in shared


def _fake_llm_json(*, dims: list[tuple[str, float, float]], reported_weighted_total: float) -> str:
    """Build fake score_and_recommend JSON the way a model might, including a
    top-level `weighted_total` that has drifted from the dimension sum — the
    exact shape of the 2026-09-03 bug (dims summed to 3.38, top-level field
    said 3.88)."""
    return json.dumps(
        {
            "dimensions": [
                {"dimension": name, "score": score, "weight": weight, "rationale": "x"}
                for name, score, weight in dims
            ],
            "weighted_total": reported_weighted_total,
            "recommendation": "apply",
            "recommendation_rationale": "x",
            "generate_pdf": True,
            "strengths": [],
            "gaps": [],
            "pdf_content": {"summary_angle": "", "top_bullets": [], "keywords": []},
        }
    )


def test_reported_score_equals_weighted_total_even_when_model_drifts() -> None:
    """Reproduces the live-run divergence: dimension scores sum to 3.38, but
    the model's own top-level weighted_total field said 3.88 (a leftover 0.5
    bump). The parsed score must be the dimension sum, not the drifted field."""
    dims = [
        ("Technical fit", 3.4, 0.30),
        ("Level fit", 4.0, 0.20),
        ("Domain fit", 2.0, 0.15),
        ("Growth trajectory", 3.0, 0.15),
        ("Company fit", 4.0, 0.20),
    ]
    expected = sum(score * weight for _, score, weight in dims)
    assert round(expected, 2) == 3.37  # the actual dimension-weighted sum

    content = _fake_llm_json(dims=dims, reported_weighted_total=3.88)
    scores = _parse_scores(content)

    assert round(scores.weighted_total, 2) == round(expected, 2)
    assert scores.weighted_total != 3.88
    # The displayed "Score:" (weighted_total) and the "Weighted total:" line
    # in the breakdown are the same field in the model (job_hunt/nodes/report.py
    # and job_hunt/cli/evaluation.py both read scores.weighted_total) — so
    # pinning this one field pins both call sites at once.


def test_identical_dimensions_yield_identical_weighted_total_and_recommendation() -> None:
    """A JD in a priority region and an identical one outside it — same
    dimension scores — must parse to the same weighted total and the same
    recommendation. immigration_context has no code-path effect on parsing;
    this pins that _parse_scores treats both runs' JSON identically."""
    dims = [
        ("Technical fit", 3.0, 0.30),
        ("Level fit", 3.0, 0.20),
        ("Domain fit", 3.0, 0.15),
        ("Growth trajectory", 3.0, 0.15),
        ("Company fit", 3.0, 0.20),
    ]
    in_region = _parse_scores(
        _fake_llm_json(dims=dims, reported_weighted_total=3.0)
    )
    outside_region = _parse_scores(
        _fake_llm_json(dims=dims, reported_weighted_total=3.0)
    )
    assert in_region.weighted_total == outside_region.weighted_total
    assert in_region.recommendation == outside_region.recommendation
