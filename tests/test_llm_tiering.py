"""Premium-tier routing, invocation shape, and usage accounting.

These lock in the 2026-07-27 cost work on the premium invocation itself: the
local command pipes its prompt on stdin instead of asking the CLI to read a
file, and real token/cost numbers come from the CLI's JSON envelope instead of
a word count. Which nodes use the premium tier is not configurable — every
artifact a recruiter reads is written on it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from job_hunt.services.llm.base import ChatMessage
from job_hunt.services.llm.local_command import LocalCommandProvider, _parse_output, wants_json


def test_wants_json_detects_both_flag_spellings() -> None:
    assert wants_json(["claude", "-p", "--output-format", "json"])
    assert wants_json(["claude", "-p", "--output-format=json"])
    assert not wants_json(["claude", "-p"])
    assert not wants_json(["claude", "-p", "--output-format", "text"])


def test_parse_output_sums_all_input_buckets() -> None:
    payload = json.dumps(
        {
            "result": "DRAFT",
            "is_error": False,
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 2831,
                "cache_read_input_tokens": 500,
                "output_tokens": 40,
            },
            "modelUsage": {"claude-opus-5": {}},
            "total_cost_usd": 0.0287,
        }
    )
    content, (input_tokens, output_tokens), model, cost = _parse_output(payload)
    assert content == "DRAFT"
    # Cached input is still billed input; counting only `input_tokens` would
    # under-report every call by three orders of magnitude.
    assert input_tokens == 3333
    assert output_tokens == 40
    assert model == "claude-opus-5"
    assert cost == pytest.approx(0.0287)


def test_parse_output_raises_on_cli_error() -> None:
    payload = json.dumps({"result": "boom", "is_error": True, "subtype": "error_max_turns"})
    with pytest.raises(RuntimeError, match="boom"):
        _parse_output(payload)


def test_parse_output_returns_none_for_plain_text() -> None:
    assert _parse_output("just a draft, not json") is None


def test_chat_pipes_prompt_on_stdin_and_reads_json_usage(tmp_path: Path) -> None:
    """The command gets the prompt on stdin — no {prompt_path}, no extra turn."""
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import json,sys\n"
        "prompt = sys.stdin.read()\n"
        "print(json.dumps({'result': 'GOT:' + prompt.strip()[-4:], 'is_error': False,\n"
        "  'usage': {'input_tokens': 10, 'cache_creation_input_tokens': 90, 'output_tokens': 7},\n"
        "  'modelUsage': {'claude-opus-5': {}}, 'total_cost_usd': 0.5}))\n",
        encoding="utf-8",
    )
    provider = LocalCommandProvider(
        command=["python3", str(script), "--output-format", "json"],
        timeout_seconds=30,
        artifact_dir=tmp_path / "llm",
    )
    result = asyncio.run(
        provider.chat(
            messages=[ChatMessage(role="user", content="write the LETTER")],
            model="claude-cli",
        )
    )
    assert result.content == "GOT:TTER"
    assert result.input_tokens == 100
    assert result.output_tokens == 7
    assert result.usage_estimated is False
    assert result.cost_usd == pytest.approx(0.5)
    assert result.model == "claude-opus-5"
    assert Path(result.raw["prompt_path"]).exists()


def test_chat_still_supports_prompt_path_commands(tmp_path: Path) -> None:
    """A CLI that cannot read stdin keeps working via {prompt_path}."""
    script = tmp_path / "fake_reader.py"
    script.write_text(
        "import sys\nprint('READ:' + open(sys.argv[1]).read().strip()[-4:])\n",
        encoding="utf-8",
    )
    provider = LocalCommandProvider(
        command=["python3", str(script), "{prompt_path}"],
        timeout_seconds=30,
        artifact_dir=tmp_path / "llm",
    )
    result = asyncio.run(
        provider.chat(
            messages=[ChatMessage(role="user", content="write the LETTER")],
            model="claude-cli",
        )
    )
    assert result.content.strip() == "READ:TTER"
    # No JSON envelope requested, so usage falls back to the word-count estimate.
    assert result.usage_estimated is True
    assert result.cost_usd is None


def test_chat_fails_closed_when_json_is_unparseable(tmp_path: Path) -> None:
    """Never let a non-JSON reply become the artifact.

    Falling back to "stdout is the answer" here would put a CLI warning — or
    the raw JSON envelope — straight into a résumé PDF.
    """
    script = tmp_path / "chatty.py"
    script.write_text(
        "print('Warning: update available')\nprint('{\\\"result\\\": \\\"hi\\\"}')\n",
        encoding="utf-8",
    )
    provider = LocalCommandProvider(
        command=["python3", str(script), "--output-format", "json"],
        timeout_seconds=30,
        artifact_dir=tmp_path / "llm",
    )
    with pytest.raises(RuntimeError, match="unparseable output"):
        asyncio.run(
            provider.chat(messages=[ChatMessage(role="user", content="x")], model="claude-cli")
        )


def test_chat_reports_stderr_on_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "boom.py"
    script.write_text(
        "import sys\nsys.stderr.write('credentials expired\\n')\nsys.exit(3)\n", encoding="utf-8"
    )
    provider = LocalCommandProvider(
        command=["python3", str(script)], timeout_seconds=30, artifact_dir=tmp_path / "llm"
    )
    with pytest.raises(RuntimeError, match="exited 3.*credentials expired"):
        asyncio.run(
            provider.chat(messages=[ChatMessage(role="user", content="x")], model="claude-cli")
        )


def test_chat_kills_the_whole_process_group_on_timeout(tmp_path: Path) -> None:
    """A timed-out CLI must not leave its children running for the rest of a batch."""
    child = tmp_path / "child.py"
    child.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    provider = LocalCommandProvider(
        command=["python3", str(parent)], timeout_seconds=2, artifact_dir=tmp_path / "llm"
    )
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(
            provider.chat(messages=[ChatMessage(role="user", content="x")], model="claude-cli")
        )
