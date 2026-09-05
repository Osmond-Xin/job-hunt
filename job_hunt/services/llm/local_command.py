from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import uuid
from pathlib import Path

from job_hunt.services.llm.base import ChatMessage, ChatResult

_JSON_OUTPUT_FLAG = "--output-format"


class LocalCommandProvider:
    """Run a local CLI (``claude -p``) as an LLM provider.

    Prompt delivery: the prompt is written to the process's **stdin** unless the
    configured command contains a ``{prompt_path}`` placeholder. Piping matters
    for cost — telling the CLI to read a file forces a second agent turn (tool
    call + full context replay), which doubled the input tokens of every premium
    call. Keep ``{prompt_path}`` out of the command unless a CLI genuinely
    cannot read stdin.

    Usage accounting: when the command asks for ``--output-format json`` the
    real token counts, model id, and USD cost are parsed from the CLI response.
    Otherwise stdout is treated as the answer and usage is word-count estimated.
    """

    provider = "local_command"

    def __init__(
        self,
        command: list[str],
        timeout_seconds: int = 180,
        artifact_dir: Path = Path("artifacts/llm"),
    ):
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.artifact_dir = artifact_dir

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        trace_name: str = "local_command.chat",
        trace_metadata: dict | None = None,
    ) -> ChatResult:
        _ = temperature, max_tokens, trace_name, trace_metadata
        prompt = render_prompt(messages)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        stem = uuid.uuid4().hex
        # The prompt is archived either way — premium artifacts are the ones
        # worth reproducing when a draft comes back wrong.
        prompt_path = self.artifact_dir / f"{stem}-prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        via_file = "{prompt_path}" in " ".join(self.command)
        command = [part.format(prompt_path=str(prompt_path)) for part in self.command]
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=None if via_file else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group: CLI wrappers spawn helpers of their own, and
            # killing only the direct child leaves those running. Over a long
            # batch the orphans accumulate.
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(None if via_file else prompt.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            await _terminate_group(proc)
            raise RuntimeError(f"premium local command timed out after {self.timeout_seconds}s") from exc
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"premium local command exited {proc.returncode}: {detail[-500:] or '(no stderr)'}"
            )

        raw_output = stdout.decode("utf-8", errors="replace")
        raw_path = self.artifact_dir / f"{stem}-output.txt"
        raw_path.write_text(raw_output, encoding="utf-8")

        if wants_json(self.command):
            parsed = _parse_output(raw_output)
            if parsed is None:
                # Fail closed. Falling back to "stdout is the answer" here
                # would put the raw JSON envelope — or whatever the CLI
                # printed instead — straight into a résumé PDF.
                raise RuntimeError(
                    "premium local command requested --output-format json but returned "
                    f"unparseable output (first 300 chars): {raw_output[:300]!r}"
                )
            content, usage, resolved_model, cost_usd = parsed
            resolved_model = resolved_model or model
            estimated = False
        else:
            content = raw_output
            usage = _estimate_usage(prompt, content)
            resolved_model = model
            cost_usd = None
            estimated = True

        return ChatResult(
            content=content,
            model=resolved_model,
            provider=self.provider,
            input_tokens=usage[0],
            output_tokens=usage[1],
            total_tokens=usage[0] + usage[1],
            tier="premium",
            invocation="local_command",
            usage_estimated=estimated,
            cost_usd=cost_usd,
            raw={"prompt_path": str(prompt_path), "artifact_path": str(raw_path)},
        )


async def _terminate_group(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the child's process group, then SIGKILL, then reap it."""
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        if proc.returncode is not None:
            break
        try:
            os.killpg(os.getpgid(proc.pid), signal_number)
        except (ProcessLookupError, PermissionError):
            break
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            continue
    if proc.returncode is None:
        # Reap regardless so the event loop does not keep the zombie around.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)


def wants_json(command: list[str]) -> bool:
    for index, part in enumerate(command):
        if part == _JSON_OUTPUT_FLAG and index + 1 < len(command):
            return command[index + 1] == "json"
        if part == f"{_JSON_OUTPUT_FLAG}=json":
            return True
    return False


def _parse_output(raw_output: str) -> tuple[str, tuple[int, int], str, float | None] | None:
    """Parse a ``--output-format json`` envelope.

    Returns ``(content, (input_tokens, output_tokens), model, cost_usd)``, or
    ``None`` when the payload is not the expected envelope. ``None`` is a hard
    error at the call site — see ``chat`` — never a fallback to raw stdout.
    """
    try:
        payload = json.loads(raw_output)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or "result" not in payload:
        return None
    if payload.get("is_error"):
        raise RuntimeError(
            f"premium local command returned an error: {payload.get('result') or payload.get('subtype')}"
        )
    usage = payload.get("usage") or {}
    # Cached input is still input the provider billed for — count all three
    # buckets or the ledger under-reports every call after the first.
    input_tokens = sum(
        int(usage.get(key) or 0)
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    output_tokens = int(usage.get("output_tokens") or 0)
    model_usage = payload.get("modelUsage") or {}
    model = next(iter(model_usage), "") if isinstance(model_usage, dict) else ""
    cost = payload.get("total_cost_usd")
    return (
        str(payload.get("result") or ""),
        (input_tokens, output_tokens),
        model,
        float(cost) if isinstance(cost, (int, float)) else None,
    )


def _estimate_usage(prompt: str, content: str) -> tuple[int, int]:
    return len(prompt.split()), len(content.split())


def render_prompt(messages: list[ChatMessage]) -> str:
    return "\n\n".join(f"## {message.role}\n\n{message.content}" for message in messages)
