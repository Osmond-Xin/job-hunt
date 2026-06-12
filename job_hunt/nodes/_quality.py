"""Generate → audit → regenerate loop for quality-gated artifacts.

The operator's standing instruction: trade tokens for quality. Every gated
artifact is audited by a second LLM pass against the hard framing rules; a
failing draft is regenerated with the auditor's issues as explicit feedback,
up to ``_MAX_ATTEMPTS`` times. The last draft is used either way — a failed
audit downgrades to a warning, never a blocked pipeline.
"""

from __future__ import annotations

import re

from job_hunt.models.state import JobHuntState
from job_hunt.nodes._llm import call_node_llm_or_fallback
from job_hunt.nodes._prompts import render
from job_hunt.services.llm.content import extract_json_object

_MAX_ATTEMPTS = 3

# Quantified tenure self-labels ("20+ years of experience", "two decades") trigger
# age/over-qualified screens. Role-scoped facts ("7-year tenure") are allowed.
TENURE_SELF_LABEL_RE = re.compile(
    r"\b\d{1,2}\s*\+?\s*(?:years?|yrs?)\b[^.\n]{0,40}\bexperience\b"
    r"|\b(?:two|three)\s+decades\b",
    re.IGNORECASE,
)

_CODE_FENCE_RE = re.compile(r"^```[a-z]*\s*\n|\n```\s*$", re.IGNORECASE)

_FEEDBACK_TEMPLATE = """{prompt}

---

### Reviewer feedback — your previous draft FAILED the quality audit

Previous draft:

{draft}

Issues to fix (fix ALL of them, then re-output the complete artifact, nothing else):

{issues}
"""


async def generate_with_audit(
    state: JobHuntState,
    *,
    node_name: str,
    prompt: str,
    prompt_version: str,
    artifact_type: str,
    temperature: float,
    max_tokens: int,
) -> tuple[str, list[str]]:
    """Run the generate/audit loop. Returns (artifact, errors).

    Empty artifact means the generator LLM was unavailable (fallback path);
    callers keep their existing degradation behaviour.
    """
    errors: list[str] = []
    draft = ""
    issues: list[str] = []

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        gen_prompt = prompt
        if draft and issues:
            gen_prompt = _FEEDBACK_TEMPLATE.format(
                prompt=prompt, draft=draft, issues="\n".join(f"- {issue}" for issue in issues)
            )
        result, gen_errors = await call_node_llm_or_fallback(
            state,
            node_name=node_name,
            prompt=gen_prompt,
            prompt_version=prompt_version,
            fallback_content="",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        errors += gen_errors
        candidate = _CODE_FENCE_RE.sub("", result.content.strip()).strip()
        if not candidate:
            return "", errors
        draft = candidate

        issues = await _audit(state, node_name=node_name, artifact_type=artifact_type, draft=draft)
        if not issues:
            return draft, errors

    errors.append(
        f"{node_name}: quality audit still failing after {_MAX_ATTEMPTS} attempts; "
        f"using last draft. Open issues: {'; '.join(issues)}"
    )
    return draft, errors


async def _audit(
    state: JobHuntState, *, node_name: str, artifact_type: str, draft: str
) -> list[str]:
    """Return the list of audit issues; empty list means pass."""
    # Deterministic gate first — free, and guarantees the hard rule even when
    # the auditor LLM is unavailable.
    violation = TENURE_SELF_LABEL_RE.search(draft)
    if violation:
        return [
            f"Tenure self-label {violation.group(0)!r} — replace with a neutral phrase; "
            "never advertise quantified tenure totals."
        ]

    prompt = render(
        "evaluate/quality_audit.md",
        artifact_type=artifact_type,
        draft=draft,
        cv=state.get("cv", ""),
        jd_text=state.get("jd_text", ""),
        mode=state.get("mode", "full"),
    )
    result, audit_errors = await call_node_llm_or_fallback(
        state,
        node_name=f"{node_name}_audit",
        prompt=prompt,
        prompt_version="evaluate/quality_audit.md:v1",
        fallback_content="",
        temperature=0.1,
        max_tokens=1500,
    )
    if audit_errors or not result.content.strip():
        # Auditor unavailable — accept the draft rather than block the run.
        return []
    data = extract_json_object(result.content) or {}
    if data.get("verdict") == "fail":
        issues = [str(issue) for issue in data.get("issues", []) if str(issue).strip()]
        return issues or ["Auditor failed the draft without specific issues; tighten rule compliance."]
    return []
