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


def _run(llm):
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
    audited = _run(llm)
    assert audited.content == "## Experience\n- Shipped X"
    assert audited.status == "passed"
    assert audited.errors == []
    assert [c["node_name"] for c in llm.calls] == ["tailor_cv", "tailor_cv_audit"]


def test_fail_then_regenerate_with_feedback(monkeypatch) -> None:
    fail = '{"verdict": "fail", "issues": ["Missing the Projects section"]}'
    llm = _ScriptedLLM(["draft one", fail, "draft two", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.content == "draft two"
    assert audited.errors == []
    # The regeneration prompt must carry the previous draft and the issues.
    regen_prompt = llm.calls[2]["prompt"]
    assert "BASE PROMPT" in regen_prompt
    assert "draft one" in regen_prompt
    assert "Missing the Projects section" in regen_prompt


def test_deterministic_tenure_gate_skips_llm_audit(monkeypatch) -> None:
    bad = "Engineer with 20+ years of experience."
    llm = _ScriptedLLM([bad, bad, bad])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    # The draft is returned for inspection but explicitly NOT verified, so
    # callers withhold it instead of shipping it.
    assert audited.content == bad
    assert audited.status == "failed"
    assert audited.verified is False
    # Three generation attempts, zero LLM audit calls (regex gate fails first).
    assert [c["node_name"] for c in llm.calls] == ["tailor_cv"] * 3
    assert any("quality audit FAILED" in err for err in audited.errors)


def test_all_attempts_fail_returns_failed_status(monkeypatch) -> None:
    fail = '{"verdict": "fail", "issues": ["too long"]}'
    llm = _ScriptedLLM(["d1", fail, "d2", fail, "d3", fail])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.content == "d3"
    assert audited.status == "failed"
    assert audited.issues == ["too long"]
    assert any("quality audit FAILED" in err and "too long" in err for err in audited.errors)


def test_empty_generation_returns_empty(monkeypatch) -> None:
    llm = _ScriptedLLM([""])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.content == ""
    assert audited.status == "skipped"
    assert len(llm.calls) == 1  # no audit on an empty draft


def test_unavailable_auditor_returns_unverified_not_passed(monkeypatch) -> None:
    """An auditor that never answered is not an auditor that approved.

    Both used to return an empty issue list, so a run with MiniMax down
    produced artifacts indistinguishable from audited ones.
    """
    async def llm(state, **kwargs):
        if kwargs["node_name"].endswith("_audit"):
            return _result(""), ["audit LLM failed"]
        return _result("good draft"), []

    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.content == "good draft"
    assert audited.status == "unavailable"
    assert audited.verified is False
    assert any("UNVERIFIED" in err for err in audited.errors)


def test_premium_generation_forwards_tier_and_audits_on_cheap(monkeypatch) -> None:
    llm = _ScriptedLLM(["## Experience\n- Shipped X", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = asyncio.run(
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
    assert audited.content == "## Experience\n- Shipped X"
    assert audited.errors == []
    assert llm.calls[0]["tier"] == "premium"  # generation on premium
    assert llm.calls[1]["tier"] == "cheap"  # audit stays on cheap


def test_premium_empty_generation_retries_on_cheap(monkeypatch) -> None:
    llm = _ScriptedLLM(["", "cheap draft", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = asyncio.run(
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
    assert audited.content == "cheap draft"
    assert llm.calls[0]["tier"] == "premium"
    assert llm.calls[1]["tier"] == "cheap"  # generation retry on cheap
    assert any("premium generation unavailable" in err for err in audited.errors)


def test_premium_regeneration_stays_on_premium(monkeypatch) -> None:
    """A regeneration keeps the premium tier.

    The draft that failed its audit is the one most likely to ship badly, so
    it must not be the one handed to the weaker model. Only the audit pass is
    cheap — that is the cross-model independent review.
    """
    fail = '{"verdict": "fail", "issues": ["Missing the Projects section"]}'
    llm = _ScriptedLLM(["draft one", fail, "draft two", _PASS])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = asyncio.run(
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
    assert audited.content == "draft two"
    assert audited.errors == []
    tiers = [(call["node_name"], call["tier"]) for call in llm.calls]
    assert tiers == [
        ("tailor_cv", "premium"),
        ("tailor_cv_audit", "cheap"),
        ("tailor_cv", "premium"),
        ("tailor_cv_audit", "cheap"),
    ]


def test_prose_auditor_reply_is_not_an_approval(monkeypatch) -> None:
    """Only an explicit "pass" verdict counts as a pass.

    The auditor used to approve anything that was not literally
    `verdict == "fail"`, so `Looks good to me` — or a truncated reply, or any
    JSON this code does not understand — silently blessed the draft.
    """
    llm = _ScriptedLLM(["draft", "Looks good to me"])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.status == "unavailable"
    assert audited.verified is False


def test_unknown_verdict_value_is_not_an_approval(monkeypatch) -> None:
    llm = _ScriptedLLM(["draft", '{"verdict": "maybe", "issues": []}'])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.status == "unavailable"


def test_pass_verdict_is_case_insensitive(monkeypatch) -> None:
    llm = _ScriptedLLM(["draft", '{"verdict": "PASS", "issues": []}'])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.status == "passed"
    assert audited.verified is True


def test_pass_object_embedded_in_prose_with_issues_is_not_an_approval(monkeypatch) -> None:
    """`extract_json_object` lifts the first object out of surrounding prose.

    So an auditor that narrates its concerns and then emits a pass object, or
    emits `pass` while still listing issues, must not be read as approval.
    """
    reply = 'Looks mostly fine. {"verdict": "pass", "issues": ["invented a metric"]}'
    llm = _ScriptedLLM(["draft", reply, "draft2", reply, "draft3", reply])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.status == "failed"
    assert audited.issues == ["invented a metric"]


def test_prose_wrapped_pass_object_is_not_an_approval(monkeypatch) -> None:
    """The reply must be JSON only for a pass to count.

    `extract_json_object` scans prose for the first object, so a chatty auditor
    that says the draft has problems and then emits a clean pass object would
    otherwise be read as approval.
    """
    reply = 'This draft has problems.\n{"verdict":"pass","issues":[]}'
    llm = _ScriptedLLM(["draft", reply])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    audited = _run(llm)
    assert audited.status == "unavailable"
    assert audited.verified is False


def test_pass_with_null_issues_is_not_an_approval(monkeypatch) -> None:
    llm = _ScriptedLLM(["draft", '{"verdict":"pass","issues":null}'])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    assert _run(llm).status == "unavailable"


def test_pass_without_an_issues_key_is_an_approval(monkeypatch) -> None:
    """Schema-clean minimal pass — no reason to distrust it."""
    llm = _ScriptedLLM(["draft", '{"verdict":"pass"}'])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    assert _run(llm).status == "passed"


def test_json_fenced_pass_is_still_an_approval(monkeypatch) -> None:
    """The audit prompt shows a ```json fence; that must not read as prose."""
    llm = _ScriptedLLM(["draft", '```json\n{"verdict":"pass","issues":[]}\n```'])
    monkeypatch.setattr(quality_module, "call_node_llm_or_fallback", llm)
    assert _run(llm).status == "passed"
