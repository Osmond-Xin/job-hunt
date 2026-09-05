"""Tests for MiniMax reasoning-model (M3) handling in MinimaxProvider."""

from __future__ import annotations

import asyncio

import httpx
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
    with pytest.raises(RuntimeError, match="even after a doubled-budget retry"):
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


# --- Transient-failure retry (529/5xx/429, dropped connections) ---
#
# These use a real httpx.AsyncClient wired to httpx.MockTransport so
# raise_for_status(), Retry-After headers, and httpx's own exception types
# behave exactly as they would against the real API. `minimax_module.httpx
# .AsyncClient` is monkeypatched to a factory that ignores the timeout kwarg
# and returns a real client bound to the given transport.


_RealAsyncClient = httpx.AsyncClient


def _mock_client_factory(transport: httpx.MockTransport):
    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=transport)

    return factory


def _queue_handler(responses: list):
    """Pop one entry per request: an int status (JSON error body), a dict of
    (status, extra_headers), an exception instance to raise, or a dict payload
    for a 200 OK.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            status, headers = item
            return httpx.Response(status, headers=headers, json={"error": "retry me"}, request=request)
        if isinstance(item, int):
            return httpx.Response(item, json={"error": "retry me"}, request=request)
        return httpx.Response(200, json=item, request=request)

    return handler


def _run_chat(provider: MinimaxProvider, model: str = "MiniMax-M2.7", max_tokens: int | None = 900):
    return asyncio.run(
        provider.chat(
            messages=[ChatMessage(role="user", content="hi")],
            model=model,
            max_tokens=max_tokens,
        )
    )


def test_529_then_success_returns_successful_content(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(minimax_module, "_sleep", fake_sleep)
    transport = httpx.MockTransport(_queue_handler([529, _OK_RESPONSE]))
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _mock_client_factory(transport))

    provider = MinimaxProvider(api_key="test-key", base_url="https://example.test")
    result = _run_chat(provider)

    assert result.content == "OK"
    assert result.raw["_retry_attempts"] == 2
    assert len(sleeps) == 1


def test_400_is_not_retried(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(minimax_module, "_sleep", fake_sleep)
    transport = httpx.MockTransport(_queue_handler([400]))
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _mock_client_factory(transport))

    provider = MinimaxProvider(api_key="test-key", base_url="https://example.test")
    with pytest.raises(httpx.HTTPStatusError):
        _run_chat(provider)
    assert sleeps == []


def test_attempt_budget_respected_then_raises(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(minimax_module, "_sleep", fake_sleep)
    monkeypatch.setattr(minimax_module.random, "uniform", lambda a, b: 0.0)
    # Always 529 — should exhaust _MAX_TRANSIENT_ATTEMPTS and raise, not hang.
    transport = httpx.MockTransport(_queue_handler([529, 529, 529, 529, 529]))
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _mock_client_factory(transport))

    provider = MinimaxProvider(api_key="test-key", base_url="https://example.test")
    with pytest.raises(httpx.HTTPStatusError):
        _run_chat(provider)
    # 3 total attempts -> 2 backoff waits in between, then the caller's
    # fallback path (call.py) is what would take over from here.
    assert len(sleeps) == minimax_module._MAX_TRANSIENT_ATTEMPTS - 1


def test_retry_after_header_is_honoured(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(minimax_module, "_sleep", fake_sleep)
    monkeypatch.setattr(minimax_module.random, "uniform", lambda a, b: 0.0)
    transport = httpx.MockTransport(_queue_handler([(429, {"Retry-After": "5"}), _OK_RESPONSE]))
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _mock_client_factory(transport))

    provider = MinimaxProvider(api_key="test-key", base_url="https://example.test")
    result = _run_chat(provider)

    assert result.content == "OK"
    # Retry-After=5, capped at _MAX_BACKOFF_SECONDS=8 (no cap applied), equal
    # jitter with random.uniform patched to 0 -> delay == base / 2.
    assert sleeps == [2.5]


def test_transient_transport_exception_is_retried(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(minimax_module, "_sleep", fake_sleep)
    transport = httpx.MockTransport(_queue_handler([httpx.ReadTimeout("timed out"), _OK_RESPONSE]))
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _mock_client_factory(transport))

    provider = MinimaxProvider(api_key="test-key", base_url="https://example.test")
    result = _run_chat(provider)

    assert result.content == "OK"
    assert result.raw["_retry_attempts"] == 2


def test_successful_first_attempt_has_no_retry_marker(monkeypatch) -> None:
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _FakeClient)
    result = _chat("MiniMax-M2.7", _OK_RESPONSE, max_tokens=900)
    assert "_retry_attempts" not in result.raw


def test_truncation_retry_is_unaffected_by_transient_retry_budget(monkeypatch) -> None:
    # The truncation retry (doubled max_tokens after finish_reason=length)
    # must still behave exactly as before, and must not be confused with a
    # transient-failure retry: each of its two HTTP attempts succeeds
    # (finish_reason handling is what drives the second call), so no backoff
    # sleep happens and no retry marker is added.
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(minimax_module, "_sleep", fake_sleep)
    monkeypatch.setattr(minimax_module.httpx, "AsyncClient", _FakeClient)
    starved = {
        "choices": [
            {"finish_reason": "length", "message": {"content": "", "reasoning_content": "x" * 500}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 300, "total_tokens": 310},
    }
    with pytest.raises(RuntimeError, match="even after a doubled-budget retry"):
        _chat("MiniMax-M3", starved)
    assert _FakeClient.last_payload["max_tokens"] == (900 + minimax_module._REASONING_HEADROOM_TOKENS) * 2
    assert sleeps == []
