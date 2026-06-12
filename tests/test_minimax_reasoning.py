"""Tests for MiniMax reasoning-model (M3) handling in MinimaxProvider."""

from __future__ import annotations

import asyncio

import pytest

from job_hunt.services.llm import minimax as minimax_module
from job_hunt.services.llm.base import ChatMessage
from job_hunt.services.llm.minimax import MinimaxProvider


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Captures the request payload and returns a canned response."""

    last_payload: dict = {}
    response_payload: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        pass

    async def post(self, url, headers=None, json=None):
        type(self).last_payload = json
        return _FakeResponse(type(self).response_payload)


def _chat(model: str, response_payload: dict, max_tokens: int | None = 900):
    _FakeClient.response_payload = response_payload
    provider = MinimaxProvider(api_key="test-key", base_url="https://example.test")
    return asyncio.run(
        provider.chat(
            messages=[ChatMessage(role="user", content="hi")],
            model=model,
            max_tokens=max_tokens,
        )
    )


_OK_RESPONSE = {
    "choices": [{"finish_reason": "stop", "message": {"content": "OK", "reasoning_content": "…"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


def test_reasoning_model_gets_max_tokens_headroom(monkeypatch) -> None:
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _FakeClient)
    result = _chat("MiniMax-M3", _OK_RESPONSE, max_tokens=900)
    assert _FakeClient.last_payload["max_tokens"] == 900 + minimax_module._REASONING_HEADROOM_TOKENS
    assert result.content == "OK"


def test_non_reasoning_model_keeps_max_tokens(monkeypatch) -> None:
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _FakeClient)
    _chat("MiniMax-M2.7", _OK_RESPONSE, max_tokens=900)
    assert _FakeClient.last_payload["max_tokens"] == 900


def test_no_max_tokens_means_no_headroom_key(monkeypatch) -> None:
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _FakeClient)
    _chat("MiniMax-M3", _OK_RESPONSE, max_tokens=None)
    assert "max_tokens" not in _FakeClient.last_payload


def test_length_finish_retries_with_doubled_budget_then_raises(monkeypatch) -> None:
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _FakeClient)
    starved = {
        "choices": [
            {"finish_reason": "length", "message": {"content": "", "reasoning_content": "x" * 500}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 300, "total_tokens": 310},
    }
    with pytest.raises(RuntimeError, match="truncated at max_tokens=9800 even after"):
        _chat("MiniMax-M3", starved)
    # Second (retry) request carried double the first budget: (900+4000)*2.
    assert _FakeClient.last_payload["max_tokens"] == (900 + minimax_module._REASONING_HEADROOM_TOKENS) * 2


def test_empty_content_with_stop_finish_does_not_raise(monkeypatch) -> None:
    # A legitimately empty answer (finish_reason=stop) flows through; only
    # reasoning starvation is an error.
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _FakeClient)
    empty_ok = {
        "choices": [{"finish_reason": "stop", "message": {"content": ""}}],
        "usage": {},
    }
    result = _chat("MiniMax-M3", empty_ok)
    assert result.content == ""
