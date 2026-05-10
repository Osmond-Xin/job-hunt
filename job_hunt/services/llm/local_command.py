from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from job_hunt.services.llm.base import ChatMessage, ChatResult


class LocalCommandProvider:
    provider = "local_command"

    def __init__(self, command: list[str], timeout_seconds: int = 180, artifact_dir: Path = Path("artifacts/llm")):
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
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(prompt)
            prompt_path = Path(handle.name)
        command = [part.format(prompt_path=str(prompt_path)) for part in self.command]
        if "{prompt_path}" not in " ".join(self.command):
            command.append(str(prompt_path))
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            proc.kill()
            raise RuntimeError(f"premium local command timed out after {self.timeout_seconds}s") from exc
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace") or "premium local command failed")
        content = stdout.decode("utf-8", errors="replace")
        raw_path = self.artifact_dir / f"{prompt_path.stem}-output.txt"
        raw_path.write_text(content, encoding="utf-8")
        estimated_input = len(prompt.split())
        estimated_output = len(content.split())
        return ChatResult(
            content=content,
            model=model,
            provider=self.provider,
            input_tokens=estimated_input,
            output_tokens=estimated_output,
            total_tokens=estimated_input + estimated_output,
            tier="premium",
            invocation="local_command",
            usage_estimated=True,
            raw={"prompt_path": str(prompt_path), "artifact_path": str(raw_path)},
        )


def render_prompt(messages: list[ChatMessage]) -> str:
    return "\n\n".join(f"## {message.role}\n\n{message.content}" for message in messages)

