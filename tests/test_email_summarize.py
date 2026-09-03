import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from job_hunt.config.models import (
    LocalLedgerConfig,
    ObservabilityConfig,
    PathsConfig,
    Settings,
)
from job_hunt.services.email.message_parser import ParsedEmail
from job_hunt.services.email.summarize import (
    build_query,
    normalize_verdict,
    parse_llm_json,
    summarize_mailbox,
    summarized_ids,
)


def test_build_query_is_broad():
    query = build_query("120d")
    assert "newer_than:120d" in query
    assert "-category:promotions" in query


def test_parse_llm_json_plain():
    assert parse_llm_json('{"job_related": true}') == {"job_related": True}


def test_parse_llm_json_fenced():
    content = 'Here you go:\n```json\n{"category": "rejection"}\n```'
    assert parse_llm_json(content) == {"category": "rejection"}


def test_parse_llm_json_embedded_prose():
    content = 'The verdict is {"category": "offer", "job_related": true} as requested.'
    assert parse_llm_json(content)["category"] == "offer"


def test_parse_llm_json_invalid_escape_recovers():
    content = '{"summary": "Rejected for Data\\_Analyst role", "job_related": true}'
    parsed = parse_llm_json(content)
    assert parsed["summary"] == "Rejected for Data\\_Analyst role"


def test_parse_llm_json_garbage_raises():
    with pytest.raises(ValueError):
        parse_llm_json("no json here")


def test_normalize_verdict_bad_category_falls_back():
    verdict = normalize_verdict({"job_related": True, "category": "weird"})
    assert verdict["category"] == "other_job_related"
    verdict = normalize_verdict({"job_related": False, "category": "weird"})
    assert verdict["category"] == "not_job_related"


def test_summarized_ids_resume(tmp_path):
    path = tmp_path / "summaries.jsonl"
    path.write_text(
        json.dumps({"message_id": "a1"}) + "\nnot json\n" + json.dumps({"message_id": "b2"}) + "\n",
        encoding="utf-8",
    )
    assert summarized_ids(path) == {"a1", "b2"}


def _fake_settings(tmp_path: Path | None = None) -> Settings:
    """Build test Settings with optional temp directory for data_dir.

    When tmp_path is provided, paths.data_dir is set to it to avoid littering
    the repository with mock directories during traced_chat calls.
    """
    paths = PathsConfig(data_dir=tmp_path) if tmp_path else PathsConfig()
    return Settings(paths=paths)


def test_summarize_mailbox_skips_done_and_appends(tmp_path, monkeypatch):
    from job_hunt.services.llm.base import ChatResult

    output = tmp_path / "summaries.jsonl"
    output.write_text(json.dumps({"message_id": "done1"}) + "\n", encoding="utf-8")

    client = MagicMock()
    client.list_messages_all.return_value = [{"id": "done1"}, {"id": "new1"}]
    client.get_message.return_value = {"raw": True}
    client.parse_message.return_value = ParsedEmail(
        message_id="new1",
        thread_id="t1",
        sender="Recruiter <r@acme.com>",
        subject="Interview invitation",
        snippet="snippet",
        body="We would like to schedule a call.",
        date=datetime(2026, 5, 1, 12, 0),
    )
    provider = AsyncMock()
    provider.provider = "minimax"
    provider.chat.return_value = ChatResult(
        content=json.dumps(
            {
                "job_related": True,
                "category": "interview_invite",
                "company": "Acme",
                "role": "AI Engineer",
                "human_touch": True,
                "action_required": True,
                "summary": "Recruiter invites to a call.",
            }
        ),
        model="minimax-model",
        provider="minimax",
        tier="cheap",
        invocation="http",
    )

    settings = _fake_settings(tmp_path)
    monkeypatch.setattr("job_hunt.services.email.summarize.GmailClient", lambda **kwargs: client)
    monkeypatch.setattr(
        "job_hunt.services.email.summarize.build_cheap_provider", lambda settings: provider
    )

    result = asyncio.run(summarize_mailbox(settings, output_path=output))

    assert result.listed == 2
    assert result.skipped_done == 1
    assert result.summarized == 1
    assert result.errors == 0
    lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["message_id"] == "new1"
    assert lines[-1]["category"] == "interview_invite"
    assert lines[-1]["company"] == "Acme"


def test_summarize_mailbox_records_error_and_continues(tmp_path, monkeypatch):
    output = tmp_path / "summaries.jsonl"

    client = MagicMock()
    client.list_messages_all.return_value = [{"id": "bad1"}]
    client.get_message.side_effect = RuntimeError("gmail down")

    settings = _fake_settings(tmp_path)
    monkeypatch.setattr("job_hunt.services.email.summarize.GmailClient", lambda **kwargs: client)
    monkeypatch.setattr(
        "job_hunt.services.email.summarize.build_cheap_provider", lambda settings: AsyncMock()
    )

    result = asyncio.run(summarize_mailbox(settings, output_path=output))

    assert result.errors == 1
    assert result.error_ids == ["bad1"]
    assert not output.exists() or output.read_text(encoding="utf-8") == ""


def test_summarize_mailbox_uses_traced_chat(tmp_path, monkeypatch):
    """Verify that summarize_mailbox calls through traced_chat and writes usage ledger.

    This test ensures the call goes through traced_chat (not provider.chat directly)
    so that the LLM cost is recorded in the usage ledger.
    """
    output = tmp_path / "summaries.jsonl"

    client = MagicMock()
    client.list_messages_all.return_value = [{"id": "msg1"}]
    client.get_message.return_value = {"raw": True}
    client.parse_message.return_value = ParsedEmail(
        message_id="msg1",
        thread_id="t1",
        sender="Recruiter <r@acme.com>",
        subject="Interview invitation",
        snippet="snippet",
        body="We would like to schedule a call.",
        date=datetime(2026, 5, 1, 12, 0),
    )
    provider = AsyncMock()
    provider.provider = "minimax"

    from job_hunt.services.llm.base import ChatResult

    provider.chat.return_value = ChatResult(
        content=json.dumps(
            {
                "job_related": True,
                "category": "interview_invite",
                "company": "Acme",
                "role": "AI Engineer",
                "human_touch": True,
                "action_required": True,
                "summary": "Recruiter invites to a call.",
            }
        ),
        model="minimax-model",
        provider="minimax",
        tier="cheap",
        invocation="http",
        input_tokens=100,
        output_tokens=50,
    )

    settings = _fake_settings(tmp_path)
    monkeypatch.setattr("job_hunt.services.email.summarize.GmailClient", lambda **kwargs: client)
    monkeypatch.setattr(
        "job_hunt.services.email.summarize.build_cheap_provider", lambda settings: provider
    )

    result = asyncio.run(summarize_mailbox(settings, output_path=output))

    assert result.summarized == 1
    # Verify usage ledger was written (the whole point of routing through traced_chat)
    ledger_path = settings.paths.data_dir / "usage-ledger.jsonl"
    assert ledger_path.exists(), f"Usage ledger not written to {ledger_path}"
    ledger_lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(ledger_lines) > 0
    ledger_record = json.loads(ledger_lines[-1])
    assert ledger_record["graph_name"] == "mailbox_summarize_graph"
    assert ledger_record["node_name"] == "summarize_message"
    assert ledger_record["model_tier"] == "cheap"


def test_parse_llm_json_invalid_escape_still_works():
    """Verify backslash escaping is preserved when using shared extractor."""
    content = '{"summary": "Rejected for Data\\_Analyst role", "job_related": true}'
    parsed = parse_llm_json(content)
    assert parsed["summary"] == "Rejected for Data\\_Analyst role"
    assert parsed["job_related"] is True
