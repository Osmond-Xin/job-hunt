"""MiniMax-driven mailbox scan: classify + summarize every email into JSONL.

Unlike the rule-based poller (``poller.py``), this pass sends each email to the
cheap LLM tier and records a structured verdict per message. Output is
append-only ``data/email-summaries.jsonl``; reruns skip already-summarized
message ids, so the scan is resumable.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from job_hunt.config.models import Settings
from job_hunt.services.prompts import render
from job_hunt.services.email.gmail_client import GmailClient
from job_hunt.services.email.message_parser import clean_email_text
from job_hunt.services.llm.base import ChatMessage
from job_hunt.services.llm.content import extract_json_object
from job_hunt.services.llm.factory import build_cheap_provider
from job_hunt.services.llm.traced import traced_chat

SUMMARY_PATH = Path("data/email-summaries.jsonl")

_BODY_CHAR_LIMIT = 4000
_MAX_TOKENS = 800

CATEGORIES = {
    "rejection",
    "interview_invite",
    "ai_assessment",
    "online_assessment",
    "application_ack",
    "recruiter_outreach",
    "offer",
    "info_request",
    "other_job_related",
    "not_job_related",
}


@dataclass
class SummarizeResult:
    listed: int = 0
    skipped_done: int = 0
    summarized: int = 0
    errors: int = 0
    error_ids: list[str] = field(default_factory=list)


def build_query(since: str) -> str:
    """Broad Gmail query — the LLM does the filtering, not the query."""
    return f"newer_than:{since} -category:promotions -category:social -category:forums"


def summarized_ids(path: Path = SUMMARY_PATH) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message_id = record.get("message_id")
        if message_id:
            ids.add(str(message_id))
    return ids


def parse_llm_json(content: str) -> dict:
    """Parse the model's JSON verdict, tolerating code fences and stray prose."""
    # Try the shared extractor first, which handles code fences, prose, and thinking tags
    result = extract_json_object(content)
    if result is not None:
        return result

    # If that fails, try escaping invalid backslashes (e.g., \_ in text)
    escaped = _escape_invalid_backslashes(content)
    result = extract_json_object(escaped)
    if result is not None:
        return result

    raise ValueError(f"no JSON object in LLM output: {content[:200]!r}")


def _escape_invalid_backslashes(text: str) -> str:
    """Escape backslashes that are not part of a valid JSON escape (e.g. ``\\_``)."""
    return re.sub(r'\\(?![\\"/bfnrtu])', r"\\\\", text)


def normalize_verdict(raw: dict) -> dict:
    category = str(raw.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        category = "other_job_related" if raw.get("job_related") else "not_job_related"
    return {
        "job_related": bool(raw.get("job_related")),
        "category": category,
        "company": str(raw.get("company") or "").strip(),
        "role": str(raw.get("role") or "").strip(),
        "human_touch": bool(raw.get("human_touch")),
        "action_required": bool(raw.get("action_required")),
        "summary": str(raw.get("summary") or "").strip(),
    }


async def summarize_mailbox(
    settings: Settings,
    since: str = "120d",
    limit: int = 0,
    output_path: Path = SUMMARY_PATH,
    concurrency: int = 3,
    progress=None,
) -> SummarizeResult:
    client = GmailClient(
        token_path=settings.email_ingest.token_path,
        credentials_path=settings.email_ingest.credentials_path,
        auth_mode=settings.email_ingest.auth_mode,
    )
    provider = build_cheap_provider(settings)
    model = settings.llm.cheap.model

    listed = client.list_messages_all(query=build_query(since), cap=limit or 2000)
    done = summarized_ids(output_path)
    pending = [item["id"] for item in listed if item.get("id") and item["id"] not in done]

    result = SummarizeResult(listed=len(listed), skipped_done=len(listed) - len(pending))
    if not pending:
        return result

    output_path.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()

    async def _one(message_id: str) -> None:
        async with semaphore:
            try:
                parsed = client.parse_message(client.get_message(message_id))
                prompt = render(
                    "email/summarize.md",
                    sender=parsed.sender,
                    subject=parsed.subject,
                    date=parsed.date.isoformat(),
                    body=clean_email_text(parsed.body or parsed.snippet)[:_BODY_CHAR_LIMIT],
                )
                chat = await traced_chat(
                    provider,
                    settings=settings,
                    messages=[ChatMessage(role="user", content=prompt)],
                    model=model,
                    node_name="summarize_message",
                    graph_name="mailbox_summarize_graph",
                    model_tier="cheap",
                    temperature=0.0,
                    max_tokens=_MAX_TOKENS,
                    metadata={"message_id": message_id},
                )
                verdict = normalize_verdict(parse_llm_json(chat.content))
                record = {
                    "message_id": parsed.message_id,
                    "thread_id": parsed.thread_id,
                    "date": parsed.date.isoformat(),
                    "sender": parsed.sender,
                    "subject": parsed.subject,
                    **verdict,
                }
            except Exception as exc:  # noqa: BLE001 — record and continue the batch
                result.errors += 1
                result.error_ids.append(message_id)
                if progress:
                    progress(f"error {message_id}: {exc}")
                return
            async with write_lock:
                with output_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            result.summarized += 1
            if progress and result.summarized % 10 == 0:
                progress(f"{result.summarized}/{len(pending)} summarized")

    await asyncio.gather(*(_one(mid) for mid in pending))
    return result
