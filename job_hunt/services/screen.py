"""LLM screening pass between deterministic triage and full evaluation.

Three stages, each an order of magnitude more expensive than the last:

    1,500 rows  --regex triage-->  ~60  --MiniMax screen-->  ~12  --Claude evaluate--> 1 application

The middle stage exists because a title is semantic and a regex is not:
"Application Strategist" is an integration role, "Solutions Consultant" is
usually sales, and no keyword list separates them reliably. It runs on the
cheap tier through `mmx`, where quota is plentiful, and never touches the
premium tier — nothing here is outward-facing.

The screen **fails open**. A row the model did not judge, or judged in a shape
this module cannot parse, is kept and marked unscreened. Dropping a posting is
the expensive mistake; carrying a few extra into a human's shortlist is not.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "MiniMax-M3"
# M3 is a reasoning model: hidden reasoning is spent before any visible token.
DEFAULT_MAX_TOKENS = 16000

SYSTEM = """You screen job postings for one specific candidate. You see only what a job
board row carries — title, employer, location — so judge the ROLE SHAPE, not the details you
cannot see. You are a filter, not a recruiter: your job is to drop what is clearly wrong and
let through what is plausibly right. When a title is ambiguous, keep it."""

BRIEF = """THE CANDIDATE

Engineer with a long architecture and delivery record — technical director at a Beijing
company (AWS case study, 10+ person team), then solo consultant delivering client systems
end to end on AWS, then a Canadian Master of Data Analytics. Recent work is applied AI:
retrieval-augmented generation over document sets, LLM agent orchestration, evaluation
harnesses, and integrations against third-party APIs. Strong Python, Java, Node.js, SQL,
AWS, REST integration. Holds a Canadian work permit to 2029; no sponsorship needed.

WHAT HE IS LOOKING FOR, in priority order

1. Roles whose immigration value is high: a permanent, full-time position in an Atlantic
   province, a territory, Saskatchewan or Manitoba, or in the public sector anywhere.
2. Applied-AI engineering — AI/LLM/RAG/agents, forward-deployed and solutions engineering,
   AI integration and automation.
3. Roles whose real content is one person owning the whole thing: first technical hire,
   digital-transformation and systems-analyst posts at small organisations, generalist
   engineering where the job is to make AI land inside a business.
4. Junior, intermediate and senior are all acceptable. Down-levelling is fine.

DROP these outright
- Staffing agencies and outsourcing intermediaries recruiting on behalf of an unnamed client.
- Roles above the reachable level: director, VP, head of, principal, staff, chief.
- Pure sales, pure marketing, pure recruiting, pure finance/accounting, procurement.
- Deeply specialised roles his background does not touch: embedded firmware, game
  engines, mechanical/civil/electrical engineering, clinical or nursing roles, trades.
- Roles requiring a Canadian security clearance, which normally requires citizenship or PR.
- Roles requiring a professional licence he does not hold (P.Eng, CPA, RN, law).

KEEP anything plausibly in scope, including titles that merely sound adjacent."""

INSTRUCTIONS = """For EVERY numbered row, output exactly one line:

<number> | KEEP or DROP | <fit 0-5> | <reason, at most 12 words>

Fit is how well the role shape matches the candidate, 5 best. Output nothing else — no
preamble, no summary, no blank lines between rows. Every number must appear exactly once."""

_LINE_RE = re.compile(
    r"^\s*(?P<index>\d+)\s*\|\s*(?P<verdict>KEEP|DROP)\s*\|\s*(?P<fit>[0-5](?:\.\d)?)\s*\|\s*(?P<reason>.*?)\s*$",
    re.I | re.M,
)


@dataclass(frozen=True)
class Screened:
    index: int
    keep: bool
    fit: float
    reason: str
    screened: bool = True


def build_prompt(rows: list[tuple[str, str, str]]) -> str:
    """`rows` are (company, role, location) in display order, numbered from 1."""
    listing = "\n".join(
        f"{i}. {role} — {company} — {location or 'location not stated'}"
        for i, (company, role, location) in enumerate(rows, start=1)
    )
    return f"{BRIEF}\n\nROWS TO SCREEN\n\n{listing}\n\n{INSTRUCTIONS}"


def parse_response(text: str, expected: int) -> dict[int, Screened]:
    """Parse the model's lines. Rows it never mentioned are simply absent."""
    out: dict[int, Screened] = {}
    for match in _LINE_RE.finditer(text or ""):
        index = int(match.group("index"))
        if not 1 <= index <= expected or index in out:
            continue
        out[index] = Screened(
            index=index,
            keep=match.group("verdict").upper() == "KEEP",
            fit=float(match.group("fit")),
            reason=match.group("reason")[:80],
        )
    return out


def screen(
    rows: list[tuple[str, str, str]],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = 900,
    runner=None,
) -> tuple[dict[int, Screened], str]:
    """Screen `rows`; returns verdicts by 1-based index and an error string.

    Rows the model skipped are filled in as kept-but-unscreened, so a partial
    or truncated response degrades into a longer shortlist rather than a
    silently shortened one.
    """
    if not rows:
        return {}, ""
    runner = runner or _run_mmx
    try:
        text, error = runner(build_prompt(rows), model, max_tokens, timeout)
    except Exception as exc:  # noqa: BLE001 - a screening failure must not be fatal
        text, error = "", str(exc)

    verdicts = parse_response(text, len(rows))
    for index in range(1, len(rows) + 1):
        if index not in verdicts:
            verdicts[index] = Screened(
                index=index, keep=True, fit=0.0, reason="not screened", screened=False
            )
    return verdicts, error


def _run_mmx(prompt: str, model: str, max_tokens: int, timeout: int) -> tuple[str, str]:
    if not shutil.which("mmx"):
        return "", "mmx not on PATH"
    # mmx reads `--messages-file -` non-blockingly and fails with EAGAIN against
    # a subprocess pipe, so it gets a real file.
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump([{"role": "user", "content": prompt}], fh, ensure_ascii=False)
        path = fh.name
    try:
        proc = subprocess.run(
            ["mmx", "text", "chat", "--messages-file", path, "--model", model,
             "--system", SYSTEM, "--max-tokens", str(max_tokens),
             "--temperature", "0.2", "--quiet"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "", f"screen timed out after {timeout}s"
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout or "").strip()[:200]
    return proc.stdout, ""
