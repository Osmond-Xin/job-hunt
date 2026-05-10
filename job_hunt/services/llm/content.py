"""Helpers for normalizing LLM response text.

Some reasoning models return hidden-thinking text inside the visible content
field, often wrapped in <think>...</think> or markdown fences. Business nodes
should consume the final answer only.
"""

from __future__ import annotations

import json
import re
from typing import Any

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", flags=re.IGNORECASE | re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>.*$", flags=re.IGNORECASE | re.DOTALL)


def normalize_llm_content(value: Any) -> str:
    text = _coerce_text(value)
    text = strip_thinking(text)
    return _strip_outer_fence(text).strip()


def strip_thinking(text: str) -> str:
    text = _THINK_BLOCK_RE.sub("", text)
    text = _OPEN_THINK_RE.sub("", text)
    return text.replace("</think>", "").strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object from LLM output.

    This is more robust than slicing from the first "{" to the last "}" because
    reasoning sections may contain braces before the final JSON.
    """
    cleaned = normalize_llm_content(text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    return str(value)


def _strip_outer_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json|markdown|md|text)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else stripped
