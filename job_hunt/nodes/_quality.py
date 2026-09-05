"""Generate → audit → regenerate loop for quality-gated artifacts.

The operator's standing instruction: trade tokens for quality. Every gated
artifact is audited by a second LLM pass against the hard framing rules; a
failing draft is regenerated with the auditor's issues as explicit feedback,
up to ``_MAX_ATTEMPTS`` times. A draft that never passes is returned with
``status="failed"`` so callers can withhold it — it is never presented as if
it had been verified.

The audit is asymmetric on purpose: a rejection is accepted from anywhere in
the auditor's reply, while an approval is only honoured when the whole reply
is the verdict object and it lists no issues. Being wrong in the direction of
"regenerate" costs tokens; being wrong the other way sends a bad résumé.

Tier split: callers pick the generation tier — premium for everything a
recruiter reads (tailored CV, cover letter, application answers), cheap for
analysis. Regenerations stay on the caller's tier. The audit pass always runs
on the cheap tier: a different model family reviewing the draft is an
independent check, not self-approval. When the premium tier is unavailable,
generation retries once on the cheap tier so the pipeline degrades instead of
stalling.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from job_hunt.models.state import JobHuntState
from job_hunt.services.llm.call import call_node_llm_or_fallback
from job_hunt.services.prompts import render
from job_hunt.services.llm.content import extract_json_object, normalize_llm_content

_MAX_ATTEMPTS = 3

# Quantified tenure self-labels ("20+ years of experience", "two decades") trigger
# age/over-qualified screens. Role-scoped facts ("7-year tenure") are allowed.
TENURE_SELF_LABEL_RE = re.compile(
    r"\b\d{1,2}\s*\+?\s*(?:years?|yrs?)\b[^.\n]{0,40}\bexperience\b"
    r"|\b(?:two|three)\s+decades\b",
    re.IGNORECASE,
)

_CODE_FENCE_RE = re.compile(r"^```[a-z]*\s*\n|\n```\s*$", re.IGNORECASE)

@dataclass
class AuditedArtifact:
    """A generated artifact plus the verdict of the audit that gated it.

    ``status`` is what callers must branch on — an artifact that never passed
    an audit must not be presented to a recruiter as if it had:

    - ``passed``      — the auditor approved this draft.
    - ``failed``      — the auditor rejected every attempt. The draft is
                        returned so it can be inspected, never shipped as-is.
    - ``unavailable`` — the auditor could not be reached. Not the artifact's
                        fault, so it is usable, but it is unverified and the
                        operator has to be told.
    - ``skipped``     — generation itself produced nothing; ``content`` empty.
    """

    content: str
    status: Literal["passed", "failed", "unavailable", "skipped"]
    errors: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.status == "passed"


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
    tier: Literal["cheap", "premium"] = "cheap",
) -> AuditedArtifact:
    """Run the generate/audit loop and report whether the result was verified.

    Callers must not treat an unverified artifact as shippable — see
    ``AuditedArtifact.status``.
    """
    errors: list[str] = []
    draft = ""
    issues: list[str] = []
    auditor_down = False

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        gen_prompt = prompt
        if draft and issues:
            gen_prompt = _FEEDBACK_TEMPLATE.format(
                prompt=prompt, draft=draft, issues="\n".join(f"- {issue}" for issue in issues)
            )
        # Every attempt keeps the caller's tier. Dropping regenerations to the
        # cheap tier would mean a draft that failed its audit — the one most
        # likely to ship badly — is the one written by the weaker model.
        attempt_tier = tier
        result, gen_errors = await call_node_llm_or_fallback(
            state,
            node_name=node_name,
            prompt=gen_prompt,
            prompt_version=prompt_version,
            fallback_content="",
            temperature=temperature,
            max_tokens=max_tokens,
            tier=attempt_tier,
        )
        errors += gen_errors
        candidate = _CODE_FENCE_RE.sub("", result.content.strip()).strip()
        if not candidate and attempt_tier == "premium":
            errors.append(
                f"{node_name}: premium generation unavailable; retrying on cheap tier"
            )
            result, gen_errors = await call_node_llm_or_fallback(
                state,
                node_name=node_name,
                prompt=gen_prompt,
                prompt_version=prompt_version,
                fallback_content="",
                temperature=temperature,
                max_tokens=max_tokens,
                tier="cheap",
            )
            errors += gen_errors
            candidate = _CODE_FENCE_RE.sub("", result.content.strip()).strip()
        if not candidate:
            return AuditedArtifact(content="", status="skipped", errors=errors)
        draft = candidate

        issues, auditor_down = await _audit(
            state, node_name=node_name, artifact_type=artifact_type, draft=draft
        )
        if auditor_down:
            errors.append(
                f"{node_name}: quality auditor unavailable; artifact is UNVERIFIED. "
                "Read it before sending it to anyone."
            )
            return AuditedArtifact(content=draft, status="unavailable", errors=errors)
        if not issues:
            return AuditedArtifact(content=draft, status="passed", errors=errors)

    errors.append(
        f"{node_name}: quality audit FAILED after {_MAX_ATTEMPTS} attempts; artifact "
        f"withheld. Open issues: {'; '.join(issues)}"
    )
    return AuditedArtifact(content=draft, status="failed", errors=errors, issues=issues)


async def _audit(
    state: JobHuntState, *, node_name: str, artifact_type: str, draft: str
) -> tuple[list[str], bool]:
    """Audit a draft. Returns ``(issues, auditor_unavailable)``.

    An empty issue list means pass — but only when the second element is
    False. "The auditor never answered" and "the auditor approved" used to be
    the same return value, which is how unverified drafts shipped silently.
    """
    # Deterministic gate first — free, and guarantees the hard rule even when
    # the auditor LLM is unavailable.
    violation = TENURE_SELF_LABEL_RE.search(draft)
    if violation:
        return (
            [
                f"Tenure self-label {violation.group(0)!r} — replace with a neutral phrase; "
                "never advertise quantified tenure totals."
            ],
            False,
        )

    prompt = render(
        "evaluate/quality_audit.md",
        artifact_type=artifact_type,
        draft=draft,
        cv=state.get("cv", ""),
        article_digest=state.get("article_digest") or "",
        jd_text=state.get("jd_text", ""),
        mode=state.get("mode", "full"),
    )
    # Audit deliberately stays on the cheap tier: a different model family
    # reviewing the premium draft is an independent check, not a rubber stamp.
    result, audit_errors = await call_node_llm_or_fallback(
        state,
        node_name=f"{node_name}_audit",
        prompt=prompt,
        prompt_version="evaluate/quality_audit.md:v1",
        fallback_content="",
        temperature=0.1,
        max_tokens=1500,
        tier="cheap",
    )
    if audit_errors or not result.content.strip():
        # Auditor unreachable. The draft is not blocked, but it is reported as
        # unverified rather than passed — the caller decides what to do.
        return [], True
    # Detecting a rejection is deliberately permissive: the first JSON object in
    # the reply counts, and either a "fail" verdict or any listed issue — even
    # alongside a "pass" — rejects the draft. Erring toward rejection only costs
    # a regeneration. (A reply whose *first* object is clean but which hides a
    # later failure verdict does not reach here as a pass either: the strict
    # check below refuses anything that is not JSON end to end.)
    loose = extract_json_object(result.content) or {}
    loose_issues = [str(issue) for issue in loose.get("issues") or [] if str(issue).strip()]
    if str(loose.get("verdict") or "").strip().lower() == "fail" or loose_issues:
        return (
            loose_issues
            or ["Auditor failed the draft without specific issues; tighten rule compliance."],
            False,
        )

    # Accepting an approval is deliberately strict. `extract_json_object` scans
    # for the first JSON object anywhere in the text, so a reply like
    # 'This draft has problems. {"verdict":"pass","issues":[]}' would otherwise
    # read as approval. The audit prompt specifies JSON only, so a pass counts
    # only when the entire reply is that verdict object.
    strict = _whole_response_object(result.content)
    if strict is None or str(strict.get("verdict") or "").strip().lower() != "pass":
        return [], True

    issues_raw = strict.get("issues", [])
    if not isinstance(issues_raw, list):
        # Schema says a list. `null`, a string, or a missing type is a reply we
        # cannot read as an approval — unverified, not passed.
        return [], True
    issues = [str(issue) for issue in issues_raw if str(issue).strip()]
    if issues:
        # "pass" while still listing problems is self-contradictory. Take the
        # problems seriously and regenerate.
        return issues, False
    return [], False


def _whole_response_object(text: str) -> dict | None:
    """Parse the reply only when it is entirely one JSON object."""
    try:
        parsed = json.loads(normalize_llm_content(text))
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
