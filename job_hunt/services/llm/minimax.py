from __future__ import annotations

import os
from typing import Literal

import httpx

from job_hunt.services.llm.base import ChatMessage, ChatResult
from job_hunt.services.llm.content import normalize_llm_content


# Reasoning models (M3+) spend completion budget on hidden reasoning before any
# visible content; a node-level max_tokens of e.g. 900 can be consumed entirely
# by reasoning, returning empty content with finish_reason="length". Headroom
# keeps node max_tokens meaning "tokens of visible answer".
_REASONING_MODEL_PREFIXES = ("MiniMax-M3",)
# Empirical: M3 burned >13k tokens of pure reasoning on a full-CV rewrite before
# emitting any visible content. 8000 + the doubled-budget retry gives complex
# generation tasks up to ~2x(task+8000) before we fail loudly.
_REASONING_HEADROOM_TOKENS = 8000


class MinimaxProvider:
    provider = "minimax"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        group_id: str | None = None,
        endpoint_style: Literal["minimax", "anthropic", "openai"] | None = None,
        proxy_token: str | None = None,
        proxy_header_name: str | None = None,
        timeout_seconds: int = 180,
    ):
        self.api_key = api_key or os.getenv("MINIMAX_API_KEY", "")
        self.base_url = (base_url or os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat")).rstrip("/")
        self.group_id = group_id or os.getenv("MINIMAX_GROUP_ID", "")
        self.endpoint_style: Literal["minimax", "anthropic", "openai"] = (
            endpoint_style or os.getenv("MINIMAX_ENDPOINT_STYLE", "minimax")
        )  # type: ignore[assignment]
        self.proxy_token = proxy_token or os.getenv("MINIMAX_PROXY_TOKEN", "")
        self.proxy_header_name = proxy_header_name or os.getenv("MINIMAX_PROXY_HEADER_NAME", "X-Proxy-Token")
        self.timeout_seconds = timeout_seconds

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        trace_name: str = "minimax.chat",
        trace_metadata: dict | None = None,
    ) -> ChatResult:
        if not self.api_key:
            raise RuntimeError("MINIMAX_API_KEY is not configured")
        _ = trace_name, trace_metadata
        if max_tokens is not None and model.startswith(_REASONING_MODEL_PREFIXES):
            max_tokens += _REASONING_HEADROOM_TOKENS

        # A "length" finish means the answer (or its hidden reasoning) was cut
        # off — structured outputs are unusable when truncated. Retry once with
        # a doubled budget, then fail loudly instead of degrading silently.
        for attempt in range(2):
            if self.endpoint_style == "anthropic":
                url, headers, payload = self._build_anthropic_request(messages, model, temperature, max_tokens)
            elif self.endpoint_style == "openai":
                url, headers, payload = self._build_openai_request(messages, model, temperature, max_tokens)
            else:
                url, headers, payload = self._build_minimax_request(messages, model, temperature, max_tokens)
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
            raw = response.json()
            if _finish_reason(raw) == "length" and max_tokens is not None and attempt == 0:
                max_tokens *= 2
                continue
            break

        content = extract_content(raw, endpoint_style=self.endpoint_style)
        if _finish_reason(raw) == "length":
            raise RuntimeError(
                f"{model} output truncated at max_tokens={max_tokens} even after a doubled-budget retry"
                + ("; no visible content returned" if not content else "")
            )
        input_tokens, output_tokens, total_tokens = extract_usage(raw)
        return ChatResult(
            content=content,
            model=model,
            provider=self.provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            tier="cheap",
            invocation="http",
            usage_estimated=total_tokens == 0,
            raw=raw,
        )

    def _base_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.proxy_token:
            headers[self.proxy_header_name] = self.proxy_token
        return headers

    def _build_minimax_request(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int | None,
    ) -> tuple[str, dict, dict]:
        payload = {
            "model": model,
            "messages": [message.model_dump() for message in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self.api_key}"
        return f"{self.base_url}/v1/text/chatcompletion_v2", headers, payload

    def _build_anthropic_request(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int | None,
    ) -> tuple[str, dict, dict]:
        system_parts = [message.content for message in messages if message.role == "system"]
        conversation = [message.model_dump() for message in messages if message.role != "system"]
        payload = {
            "model": model,
            "messages": conversation,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        headers = self._base_headers()
        headers["x-api-key"] = self.api_key
        headers["anthropic-version"] = "2023-06-01"
        return self.base_url, headers, payload

    def _build_openai_request(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int | None,
    ) -> tuple[str, dict, dict]:
        payload = {
            "model": model,
            "messages": [message.model_dump() for message in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url
        if url.endswith("/v1"):
            url = f"{url}/chat/completions"
        elif not url.endswith("/chat/completions"):
            url = f"{url.rstrip('/')}/v1/chat/completions"
        return url, headers, payload


def _finish_reason(raw: dict) -> str:
    choices = raw.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason") or ""
    return ""


def extract_content(raw: dict, endpoint_style: str = "minimax") -> str:
    if endpoint_style == "anthropic":
        parts = raw.get("content") or []
        text_parts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        if text_parts:
            return normalize_llm_content("\n".join(part for part in text_parts if part))
        return normalize_llm_content(raw.get("completion") or raw.get("content") or "")
    choices = raw.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content") or choices[0].get("text") or ""
        return normalize_llm_content(content)
    return normalize_llm_content(raw.get("reply") or raw.get("content") or "")


def extract_usage(raw: dict) -> tuple[int, int, int]:
    usage = raw.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    return input_tokens, output_tokens, total_tokens
