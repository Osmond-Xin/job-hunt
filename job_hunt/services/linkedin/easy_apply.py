"""LinkedIn Easy Apply multi-step driver.

Mirrors ``services.workday._workday_advance_all_steps`` in spirit but is
adapted to LinkedIn's modal-based flow:

- Steps live inside a ``role="dialog"`` modal, not a full page.
- Navigation buttons are labelled ``Next`` (advance), ``Review`` (final
  pre-submit step), and ``Submit application`` (final submit). The driver
  never clicks ``Submit application`` itself — auto-submit is a gated
  caller-level decision (see :class:`AutoSubmitOutcome`).
- A "Save this application?" dismiss modal can appear when the user navigates
  away mid-flow. We do not close it here; the caller decides.

The driver returns a structured :class:`EasyApplyResult` so the CLI can:

- emit ``filled[]`` / ``skipped[]`` lines into ``apply-review.json``,
- decide whether to fire the auto-submit click,
- and (in tests) assert on the per-step trace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from job_hunt.services.linkedin.detect import (
    KNOWN_STEPS,
    is_easy_apply_modal_open,
    is_linkedin_job_url,
    is_linkedin_login_url,
    normalise_step_heading,
)
from job_hunt.services.linkedin.fields import (
    FIELD_EMAIL,
    FIELD_FIRST_NAME,
    FIELD_FULL_NAME,
    FIELD_LAST_NAME,
    FIELD_LINKEDIN,
    FIELD_LOCATION,
    FIELD_PHONE,
    FIELD_PHONE_COUNTRY,
    FIELD_WEBSITE,
    classify_label,
    country_code_best_match,
    yes_no_answer,
    years_of_experience_answer,
)


# Hard caps. LinkedIn Easy Apply currently maxes out at 4 steps in production;
# 6 leaves headroom for future additions without letting a broken DOM spin.
MAX_STEPS = 6
STEP_CHANGE_TIMEOUT_MS = 15000


# Outcome codes returned by the driver. Stable identifiers so callers can
# branch + tests can assert without depending on free-form strings.
OUTCOME_REACHED_REVIEW = "linkedin_easy_apply.reached_review"
OUTCOME_SUBMITTED = "linkedin_easy_apply.submitted"
OUTCOME_STUCK = "linkedin_easy_apply.stuck"
OUTCOME_LOGIN_REQUIRED = "linkedin_easy_apply.login_required"
OUTCOME_NOT_EASY_APPLY = "linkedin_easy_apply.not_easy_apply"
OUTCOME_MODAL_NOT_OPENED = "linkedin_easy_apply.modal_not_opened"


@dataclass
class EasyApplyResult:
    """Structured result of an Easy Apply run.

    ``outcome`` is one of the ``OUTCOME_*`` constants. ``filled`` / ``skipped`` /
    ``answers`` mirror the shapes used by the existing Workday flow so the
    CLI's ``apply-review.json`` writer can consume them unchanged.
    """

    outcome: str
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    answers: list[dict[str, str]] = field(default_factory=list)
    required_empty: list[str] = field(default_factory=list)
    steps_visited: list[str] = field(default_factory=list)
    submitted: bool = False


# Injected helper signatures. The driver itself never touches Playwright
# directly so tests can mock these with ``AsyncMock``.
ClickFn = Callable[..., Awaitable[bool]]              # (page, name_or_label) -> bool
FillFn = Callable[..., Awaitable[bool]]               # (page, label, value) -> bool
DropdownFn = Callable[..., Awaitable[bool]]           # (page, label, option) -> bool
DropdownOptionsFn = Callable[..., Awaitable[list[str]]]  # (page, label) -> options
RadioFn = Callable[..., Awaitable[bool]]              # (page, question, choice) -> bool
AttachFn = Callable[..., Awaitable[bool]]             # (page, pdf) -> bool
ReadHeadingFn = Callable[..., Awaitable[str]]         # (page) -> heading
ReadRequiredFn = Callable[..., Awaitable[list[str]]]  # (page) -> empty labels
ReadFieldsFn = Callable[..., Awaitable[list[dict[str, Any]]]]
# (page) -> [{"label": str, "kind": "text|textarea|dropdown|radio", "options": [...]}]
AnswerLookupFn = Callable[[str, dict[str, Any] | None], str]
# (question, report_context) -> answer or ""


@dataclass
class Helpers:
    """Bundle of injected Playwright helpers.

    Grouped into a dataclass so the dispatcher signature stays manageable.
    Each callable takes ``page`` as the first argument; the dispatcher passes
    the live Playwright page through unchanged.
    """

    click_by_name: ClickFn
    fill_by_label: FillFn
    select_dropdown: DropdownFn
    dropdown_options: DropdownOptionsFn
    select_radio: RadioFn
    attach_resume: AttachFn
    read_modal_heading: ReadHeadingFn
    read_required_empty: ReadRequiredFn
    read_modal_fields: ReadFieldsFn
    answer_lookup: AnswerLookupFn


# --- Top-level driver -------------------------------------------------------


async def run_easy_apply(
    page,
    *,
    values: dict[str, Any],
    pdf: Optional[Path],
    company: Optional[str],
    role: Optional[str],
    report_context: Optional[dict[str, Any]],
    helpers: Helpers,
    auto_submit: bool = False,
    page_url: Optional[str] = None,
) -> EasyApplyResult:
    """Walk the Easy Apply modal up to (and optionally past) the Review step.

    ``auto_submit`` is the per-call gate. The caller is expected to have
    already AND-ed:

    - the CLI ``--auto-submit`` flag,
    - ``apply.auto_submit_enabled`` from ``profile.yml``,
    - ``mode == "full"``,

    before passing True here. The driver applies the last-mile gate
    (``required_empty == []`` on the Review step) and emits a stable outcome
    code either way.
    """
    result = EasyApplyResult(outcome=OUTCOME_NOT_EASY_APPLY)

    url = page_url or _safe_url(page)
    if is_linkedin_login_url(url):
        result.outcome = OUTCOME_LOGIN_REQUIRED
        result.skipped.append(
            "LinkedIn session is not logged in (redirected to login / "
            "checkpoint). Sign in manually in the persistent browser profile "
            "and re-run."
        )
        return result
    if not is_linkedin_job_url(url):
        return result

    opened = await _open_easy_apply(page, helpers)
    if not opened:
        result.outcome = OUTCOME_MODAL_NOT_OPENED
        result.skipped.append(
            "LinkedIn 'Easy Apply' button was not found on the job page. This "
            "posting likely redirects to an external ATS — run the generic apply "
            "flow against that URL instead."
        )
        return result

    prev_heading = ""
    for _ in range(MAX_STEPS):
        heading_raw = await _safe(helpers.read_modal_heading(page), default="")
        heading = normalise_step_heading(heading_raw)
        if heading:
            result.steps_visited.append(heading)

        if heading == "Review":
            return await _handle_review(
                page,
                result=result,
                helpers=helpers,
                auto_submit=auto_submit,
            )

        # Fill whatever the current step is asking for. ``heading`` may be ""
        # when LinkedIn changes a label; in that case we still try the
        # generic question pass over the visible fields.
        await _fill_current_step(
            page,
            result=result,
            heading=heading,
            values=values,
            pdf=pdf,
            company=company,
            role=role,
            report_context=report_context,
            helpers=helpers,
        )

        advanced = await _click_advance(page, helpers)
        if not advanced:
            result.outcome = OUTCOME_STUCK
            result.skipped.append(
                f"LinkedIn Easy Apply stuck on step '{heading or heading_raw}' — "
                "no Next / Review button could be clicked. Resolve required "
                "fields in the modal and re-run."
            )
            result.required_empty = await _safe(
                helpers.read_required_empty(page), default=[]
            )
            return result

        # Detect the step actually changed; if not we are stuck on the same
        # step (validation error somewhere we did not fill). Bail.
        new_heading_raw = await _safe(helpers.read_modal_heading(page), default="")
        new_heading = normalise_step_heading(new_heading_raw)
        if new_heading and new_heading == heading and heading == prev_heading:
            result.outcome = OUTCOME_STUCK
            result.required_empty = await _safe(
                helpers.read_required_empty(page), default=[]
            )
            result.skipped.append(
                f"LinkedIn Easy Apply did not advance past '{heading}'. "
                "Validation likely failed on a field we could not fill."
            )
            return result
        prev_heading = heading

    # Ran out of step budget without reaching Review. Treat as stuck.
    result.outcome = OUTCOME_STUCK
    result.skipped.append(
        f"LinkedIn Easy Apply exceeded {MAX_STEPS} steps without reaching "
        "Review. Inspect the modal manually."
    )
    return result


# --- Per-step orchestration -------------------------------------------------


async def _open_easy_apply(page, helpers: Helpers) -> bool:
    """Click the Easy Apply trigger if the modal is not already open."""
    if await _safe(is_easy_apply_modal_open(page), default=False):
        return True
    clicked = await _safe(
        helpers.click_by_name(page, "Easy Apply"), default=False
    )
    if not clicked:
        return False
    return await _safe(is_easy_apply_modal_open(page), default=False)


async def _fill_current_step(
    page,
    *,
    result: EasyApplyResult,
    heading: str,
    values: dict[str, Any],
    pdf: Optional[Path],
    company: Optional[str],
    role: Optional[str],
    report_context: Optional[dict[str, Any]],
    helpers: Helpers,
) -> None:
    """Dispatch fill logic for the current modal step.

    Mutates ``result`` in place rather than returning a tuple — keeps the
    call-site readable.
    """
    if heading == "Resume":
        await _fill_resume_step(page, result=result, pdf=pdf, helpers=helpers)
        return

    fields = await _safe(helpers.read_modal_fields(page), default=[])
    for entry in fields:
        await _fill_field(
            page,
            result=result,
            entry=entry,
            values=values,
            company=company,
            role=role,
            report_context=report_context,
            helpers=helpers,
        )


async def _fill_resume_step(
    page,
    *,
    result: EasyApplyResult,
    pdf: Optional[Path],
    helpers: Helpers,
) -> None:
    if pdf is None:
        result.skipped.append(
            "LinkedIn Easy Apply Resume step: no --pdf supplied. Upload a "
            "resume in LinkedIn or re-run with --pdf <resume.pdf>."
        )
        return
    attached = await _safe(helpers.attach_resume(page, pdf), default=False)
    if attached:
        result.filled.append(f"LinkedIn Resume: {pdf.name}")
    else:
        result.skipped.append(
            f"LinkedIn Resume step: could not attach {pdf.name}. The selected "
            "resume on file may already be the desired one."
        )


async def _fill_field(
    page,
    *,
    result: EasyApplyResult,
    entry: dict[str, Any],
    values: dict[str, Any],
    company: Optional[str],
    role: Optional[str],
    report_context: Optional[dict[str, Any]],
    helpers: Helpers,
) -> None:
    label = (entry.get("label") or "").strip()
    kind = (entry.get("kind") or "").strip()
    options = entry.get("options") or []
    if not label:
        return

    # Identity / contact fields first (highest-confidence path).
    classified = classify_label(label)
    if classified and kind in {"text", "input"}:
        target = _value_for_classified(classified, values)
        if target and await _safe(
            helpers.fill_by_label(page, label, target), default=False
        ):
            result.filled.append(f"LinkedIn {label}: {_short(target)}")
            return

    if classified == FIELD_PHONE_COUNTRY and kind == "dropdown":
        country = (values.get("country") or "").strip() or "Canada"
        choice = country_code_best_match(country, options)
        if choice and await _safe(
            helpers.select_dropdown(page, label, choice), default=False
        ):
            result.filled.append(f"LinkedIn {label}: {choice}")
            return

    if classified == FIELD_LOCATION and kind in {"text", "input"}:
        target = values.get("location", "") or values.get("city", "")
        if target and await _safe(
            helpers.fill_by_label(page, label, target), default=False
        ):
            result.filled.append(f"LinkedIn {label}: {_short(target)}")
            return

    # Yes/No radios (work authorization, terms, etc.).
    if kind == "radio":
        choice = yes_no_answer(label)
        if choice and await _safe(
            helpers.select_radio(page, label, choice), default=False
        ):
            result.filled.append(f"LinkedIn radio: {_short(label)} → {choice}")
            result.answers.append({"question": label, "answer": choice})
            return
        result.skipped.append(f"LinkedIn radio (needs review): {_short(label)}")
        return

    # Years-of-experience numeric inputs.
    if kind in {"text", "input"}:
        years = years_of_experience_answer(label)
        if years and await _safe(
            helpers.fill_by_label(page, label, years), default=False
        ):
            result.filled.append(f"LinkedIn {label}: {years}")
            result.answers.append({"question": label, "answer": years})
            return

    # Generic textarea fall-through — let the answer-lookup function decide.
    if kind == "textarea":
        answer = helpers.answer_lookup(label, report_context)
        if answer and await _safe(
            helpers.fill_by_label(page, label, answer), default=False
        ):
            result.filled.append(f"LinkedIn textarea: {_short(label)}")
            result.answers.append({"question": label, "answer": answer})
            return
        result.skipped.append(f"LinkedIn textarea (needs review): {_short(label)}")
        return

    # Unknown dropdown / radio / unmatched text — record for the user.
    result.skipped.append(f"LinkedIn field (needs review): {_short(label)}")


def _value_for_classified(classified: str, values: dict[str, Any]) -> str:
    mapping = {
        FIELD_EMAIL: values.get("email", ""),
        FIELD_PHONE: values.get("phone", ""),
        FIELD_LINKEDIN: values.get("linkedin", ""),
        FIELD_WEBSITE: values.get("portfolio", ""),
        FIELD_LOCATION: values.get("location", ""),
        FIELD_FULL_NAME: values.get("name", ""),
        FIELD_FIRST_NAME: values.get("first_name", ""),
        FIELD_LAST_NAME: values.get("last_name", ""),
    }
    return str(mapping.get(classified, "") or "")


# --- Review handling --------------------------------------------------------


async def _handle_review(
    page,
    *,
    result: EasyApplyResult,
    helpers: Helpers,
    auto_submit: bool,
) -> EasyApplyResult:
    """Final step: optionally fire auto-submit, otherwise stop here."""
    required = await _safe(helpers.read_required_empty(page), default=[])
    result.required_empty = list(required)

    if not auto_submit:
        result.outcome = OUTCOME_REACHED_REVIEW
        return result

    if required:
        result.outcome = OUTCOME_REACHED_REVIEW
        result.skipped.append(
            "LinkedIn auto-submit skipped: required fields still empty on "
            "Review (" + ", ".join(required[:5]) + ")."
        )
        return result

    clicked = await _safe(
        helpers.click_by_name(page, "Submit application"), default=False
    )
    if not clicked:
        result.outcome = OUTCOME_REACHED_REVIEW
        result.skipped.append(
            "LinkedIn auto-submit skipped: Submit application button not found "
            "on Review step."
        )
        return result
    result.outcome = OUTCOME_SUBMITTED
    result.submitted = True
    result.filled.append("LinkedIn Submit application: clicked")
    return result


# --- Advance + utility ------------------------------------------------------


_ADVANCE_LABELS = ("Review", "Continue to next step", "Next")


async def _click_advance(page, helpers: Helpers) -> bool:
    """Try advance buttons in priority order: Review → Continue → Next."""
    for label in _ADVANCE_LABELS:
        if await _safe(helpers.click_by_name(page, label), default=False):
            return True
    return False


async def _safe(awaitable, *, default):
    """Run ``awaitable`` and return ``default`` on any exception."""
    try:
        return await awaitable
    except Exception:
        return default


def _safe_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _short(text: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# Re-export step labels so callers can build matchers without importing detect.
__all__ = (
    "AnswerLookupFn",
    "EasyApplyResult",
    "Helpers",
    "KNOWN_STEPS",
    "MAX_STEPS",
    "OUTCOME_LOGIN_REQUIRED",
    "OUTCOME_MODAL_NOT_OPENED",
    "OUTCOME_NOT_EASY_APPLY",
    "OUTCOME_REACHED_REVIEW",
    "OUTCOME_STUCK",
    "OUTCOME_SUBMITTED",
    "STEP_CHANGE_TIMEOUT_MS",
    "run_easy_apply",
)
