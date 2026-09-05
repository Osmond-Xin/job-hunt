from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatResult(BaseModel):
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tier: Literal["cheap", "premium"]
    invocation: Literal["http", "local_command"]
    usage_estimated: bool = False
    # Real USD cost when the provider reports it (claude -p --output-format
    # json). None means "not reported", never "free".
    cost_usd: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ChatProvider(Protocol):
    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        trace_name: str = "llm.chat",
        trace_metadata: dict[str, Any] | None = None,
    ) -> ChatResult:
        ...

