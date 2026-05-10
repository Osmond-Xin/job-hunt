from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    name: str = "job-hunt"
    env: str = "local"
    timezone: str = "America/Toronto"


class PathsConfig(BaseModel):
    data_dir: Path = Path("data")
    cache_dir: Path = Path("cache")
    artifact_dir: Path = Path("artifacts")
    reports_dir: Path = Path("reports")
    output_dir: Path = Path("output")


class LlmTierConfig(BaseModel):
    provider: str
    model: str = ""
    invocation: Literal["http", "local_command"] = "http"
    temperature: float = 0.2
    base_url: str = ""
    endpoint_style: Literal["minimax", "anthropic", "openai"] = "minimax"
    api_key_env: str = ""
    proxy_token_env: str = ""
    proxy_header_name: str = "X-Proxy-Token"
    command: list[str] = Field(default_factory=list)
    timeout_seconds: int = 180


class LlmConfig(BaseModel):
    cheap: LlmTierConfig = Field(
        default_factory=lambda: LlmTierConfig(provider="minimax", invocation="http")
    )
    premium: LlmTierConfig = Field(
        default_factory=lambda: LlmTierConfig(
            provider="local_command", invocation="local_command", command=["claude", "-p"]
        )
    )


class LocalLedgerConfig(BaseModel):
    enabled: bool = True


class LangSmithConfig(BaseModel):
    enabled: bool = False
    project: str = "job-hunt-dev"
    respect_env: bool = True
    fail_closed: bool = False
    monthly_trace_budget: int = 5000
    soft_limit: int = 4000
    hard_limit: int = 4900


class ObservabilityConfig(BaseModel):
    local_ledger: LocalLedgerConfig = Field(default_factory=LocalLedgerConfig)
    langsmith: LangSmithConfig = Field(default_factory=LangSmithConfig)


class LocalLogSinkConfig(BaseModel):
    enabled: bool = True
    path: Path = Path("data/activity-log.jsonl")


class SlackSinkConfig(BaseModel):
    enabled: bool = False
    webhook_secret_ref: str = "env:JOB_HUNT_SLACK_WEBHOOK_URL"
    min_level: Literal["debug", "info", "warning", "error"] = "info"
    include_sensitive: bool = False
    events: list[str] = Field(default_factory=list)


class ActivitySinksConfig(BaseModel):
    local_log: LocalLogSinkConfig = Field(default_factory=LocalLogSinkConfig)
    slack: SlackSinkConfig = Field(default_factory=SlackSinkConfig)


class ActivityConfig(BaseModel):
    sinks: ActivitySinksConfig = Field(default_factory=ActivitySinksConfig)


class EmailLabelsConfig(BaseModel):
    processed: str = "JobHunt/Processed"
    needs_review: str = "JobHunt/NeedsReview"
    imported: str = "JobHunt/Imported"


class EmailIngestConfig(BaseModel):
    enabled: bool = True
    provider: Literal["gmail"] = "gmail"
    account: str = "me"
    auth_mode: Literal["oauth_file", "gcloud_adc"] = "gcloud_adc"
    mode: Literal["polling", "watch"] = "polling"
    poll_interval_minutes: int = 30
    token_path: Path = Path("storage/oauth/gmail-token.json")
    credentials_path: Path = Path("credentials.json")
    query: str = ""
    label_ids: list[str] = Field(default_factory=list)
    labels: EmailLabelsConfig = Field(default_factory=EmailLabelsConfig)


class WebSearchConfig(BaseModel):
    """Web search provider config — optional, defaults to disabled."""

    provider: Literal["brave", "none"] = "none"
    api_key_env: str = "BRAVE_API_KEY"
    count: int = 10
    freshness: str = "pw"  # pd / pw / pm / py
    timeout_s: float = 10.0


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    email_ingest: EmailIngestConfig = Field(default_factory=EmailIngestConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)


def _expand_env(value):
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return _expand_env(data)


def load_settings(path: Path = Path("config/settings.yml")) -> Settings:
    load_dotenv_file(Path(".env"))
    fallback = Path("config/settings.example.yml")
    data = load_yaml(path if path.exists() else fallback)
    settings = Settings.model_validate(data)
    apply_llm_env_overrides(settings)

    env_enabled = os.getenv("JOB_HUNT_LANGSMITH_ENABLED")
    if settings.observability.langsmith.respect_env and env_enabled is not None:
        settings.observability.langsmith.enabled = env_enabled.lower() in {"1", "true", "yes", "on"}
    env_project = os.getenv("LANGSMITH_PROJECT")
    if settings.observability.langsmith.respect_env and env_project:
        settings.observability.langsmith.project = env_project
    return settings


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def apply_llm_env_overrides(settings: Settings) -> None:
    cheap = settings.llm.cheap
    if os.getenv("MINIMAX_MODEL"):
        cheap.model = os.environ["MINIMAX_MODEL"]
    if os.getenv("MINIMAX_BASE_URL"):
        cheap.base_url = os.environ["MINIMAX_BASE_URL"]
    if os.getenv("MINIMAX_ENDPOINT_STYLE"):
        cheap.endpoint_style = os.environ["MINIMAX_ENDPOINT_STYLE"]  # type: ignore[assignment]
    if os.getenv("MINIMAX_PROXY_HEADER_NAME"):
        cheap.proxy_header_name = os.environ["MINIMAX_PROXY_HEADER_NAME"]
