"""The score gate on `draft_application_answers`.

It sat at 4.5 while the scorer's "apply" band was 4.0. When that band dropped to
3.5 on 2026-08-16 the gate stopped firing entirely — the highest score of that
whole day was 4.35 — so the node quietly produced nothing for every role the
operator actually applied to. These tests pin the gate to the apply band.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from job_hunt.nodes import personalize


@dataclass
class _Scores:
    weighted_total: float


@pytest.fixture
def no_llm(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record LLM calls instead of making them."""
    calls: list[str] = []

    async def _fake(state, *, node_name, prompt, **kwargs):  # noqa: ANN001
        calls.append(node_name)

        class _Result:
            content = "drafted"

        return _Result(), []

    monkeypatch.setattr(personalize, "call_node_llm_or_fallback", _fake)
    monkeypatch.setattr(personalize, "render", lambda *a, **k: "prompt")
    return calls


def _run(score: float | None) -> dict:
    state = {"scores": _Scores(score) if score is not None else None}
    return asyncio.run(personalize.draft_application_answers(state, None))


def test_an_applied_to_score_gets_its_answers_drafted(no_llm: list[str]) -> None:
    result = _run(3.5)
    assert no_llm == ["draft_application_answers"]
    assert result["evaluation_blocks"]["draft_answers"] == "drafted"


def test_the_2026_08_16_top_score_would_have_drafted(no_llm: list[str]) -> None:
    """4.35 was the best score of the day the old 4.5 gate silently blocked."""
    _run(4.35)
    assert no_llm == ["draft_application_answers"]


def test_a_skip_score_spends_nothing(no_llm: list[str]) -> None:
    result = _run(2.9)
    assert no_llm == []
    assert result == {"errors": []}


def test_no_scores_spends_nothing(no_llm: list[str]) -> None:
    assert _run(None) == {"errors": []}
    assert no_llm == []
