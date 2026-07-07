"""Tests for the generate→audit→regenerate loop in job_hunt.nodes._quality."""

from __future__ import annotations

import asyncio

from job_hunt.nodes import _quality as quality_module
from job_hunt.nodes._quality import generate_with_audit
from job_hunt.services.llm.base import ChatResult


def _result(content: str) -> ChatResult:
    return ChatResult(
        content=content, model="fake", provider="local", tier="cheap", invocation="http"
    )


class _ScriptedLLM:
    """Returns scripted responses keyed by call order; records every call."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def __call__(self, state, **kwargs):
        self.calls.append(kwargs)
        return _result(self.responses.pop(0)), []


_STATE = {"cv": "## Experience\n- Shipped X", "jd_text": "Backend role.", "mode": "full"}
_PASS = '{"verdict": "pass", "issues": []}'


def _run(llm) -> tuple[str, list[str]]:
    return asyncio.run(
        generate_with_audit(
            dict(_STATE),
            node_name="tailor_cv",
            prompt="BASE PROMPT",
            prompt_version="x:v1",
            artifact_type="tailored CV",
            temperature=0.2,
            max_tokens=100,
        )
    )


def test_pass_on_first_attempt(monkeypatch) -> None:
    llm = _ScriptedLLM(["## Experience\n- Shipped X", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = _run(llm)
    assert artifact == "## Experience\n- Shipped X"
    assert errors == []
    assert [c["node_name"] for c in llm.calls] == ["tailor_cv", "tailor_cv_audit"]


def test_fail_then_regenerate_with_feedback(monkeypatch) -> None:
    fail = '{"verdict": "fail", "issues": ["Missing the Projects section"]}'
    llm = _ScriptedLLM(["draft one", fail, "draft two", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = _run(llm)
    assert artifact == "draft two"
    assert errors == []
    # The regeneration prompt must carry the previous draft and the issues.
    regen_prompt = llm.calls[2]["prompt"]
    assert "BASE PROMPT" in regen_prompt
    assert "draft one" in regen_prompt
    assert "Missing the Projects section" in regen_prompt


def test_deterministic_tenure_gate_skips_llm_audit(monkeypatch) -> None:
    bad = "Engineer with 20+ years of experience."
    llm = _ScriptedLLM([bad, bad, bad])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = _run(llm)
    assert artifact == bad
    # Three generation attempts, zero LLM audit calls (regex gate fails first).
    assert [c["node_name"] for c in llm.calls] == ["tailor_cv"] * 3
    assert any("quality audit still failing" in err for err in errors)


def test_all_attempts_fail_keeps_last_draft(monkeypatch) -> None:
    fail = '{"verdict": "fail", "issues": ["too long"]}'
    llm = _ScriptedLLM(["d1", fail, "d2", fail, "d3", fail])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = _run(llm)
    assert artifact == "d3"
    assert any("quality audit still failing" in err and "too long" in err for err in errors)


def test_empty_generation_returns_empty(monkeypatch) -> None:
    llm = _ScriptedLLM([""])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = _run(llm)
    assert artifact == ""
    assert len(llm.calls) == 1  # no audit on an empty draft


def test_unavailable_auditor_accepts_draft(monkeypatch) -> None:
    async def llm(state, **kwargs):
        if kwargs["node_name"].endswith("_audit"):
            return _result(""), ["audit LLM failed"]
        return _result("good draft"), []

    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = _run(llm)
    assert artifact == "good draft"
    assert errors == []


def test_premium_generation_forwards_tier_and_audits_on_cheap(monkeypatch) -> None:
    llm = _ScriptedLLM(["## Experience\n- Shipped X", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = asyncio.run(
        generate_with_audit(
            dict(_STATE),
            node_name="tailor_cv",
            prompt="BASE PROMPT",
            prompt_version="x:v1",
            artifact_type="tailored CV",
            temperature=0.2,
            max_tokens=100,
            tier="premium",
        )
    )
    assert artifact == "## Experience\n- Shipped X"
    assert errors == []
    assert llm.calls[0]["tier"] == "premium"  # generation on premium
    assert llm.calls[1]["tier"] == "cheap"  # audit stays on cheap


def test_premium_empty_generation_retries_on_cheap(monkeypatch) -> None:
    llm = _ScriptedLLM(["", "cheap draft", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    artifact, errors = asyncio.run(
        generate_with_audit(
            dict(_STATE),
            node_name="tailor_cv",
            prompt="BASE PROMPT",
            prompt_version="x:v1",
            artifact_type="tailored CV",
            temperature=0.2,
            max_tokens=100,
            tier="premium",
        )
    )
    assert artifact == "cheap draft"
    assert llm.calls[0]["tier"] == "premium"
    assert llm.calls[1]["tier"] == "cheap"  # generation retry on cheap
    assert any("premium generation unavailable" in err for err in errors)
