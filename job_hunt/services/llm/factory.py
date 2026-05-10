from __future__ import annotations

import os
from pathlib import Path

from job_hunt.config.models import LlmTierConfig, Settings
from job_hunt.services.llm.local_command import LocalCommandProvider
from job_hunt.services.llm.minimax import MinimaxProvider


def build_cheap_provider(settings: Settings):
    return build_provider(settings.llm.cheap)


def build_premium_provider(settings: Settings):
    return build_provider(settings.llm.premium)


def build_provider(config: LlmTierConfig):
    if config.provider == "minimax":
        return MinimaxProvider(
            api_key=os.getenv(config.api_key_env) if config.api_key_env else None,
            base_url=config.base_url or None,
            endpoint_style=config.endpoint_style,
            proxy_token=os.getenv(config.proxy_token_env) if config.proxy_token_env else None,
            proxy_header_name=config.proxy_header_name,
            timeout_seconds=config.timeout_seconds,
        )
    if config.provider == "local_command":
        return LocalCommandProvider(
            command=config.command,
            timeout_seconds=config.timeout_seconds,
            artifact_dir=Path("artifacts/llm"),
        )
    raise ValueError(f"Unsupported LLM provider: {config.provider}")
