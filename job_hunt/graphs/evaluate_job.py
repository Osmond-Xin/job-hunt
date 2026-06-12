"""evaluate_job_graph — end-to-end single-job evaluation.

Graph topology (sequential analysis after classify_archetype):

    load_context
        └─> extract_jd
                └─> verify_active ──(inactive)──> mark_unavailable ──> END
                        └─(active)─> eligibility_gate
                                        ├─(blocked)─> mark_ineligible ──> END
                                        └─(eligible | unknown)─> classify_archetype
                                        └─> cv_match
                                             └─> role_summary
                                                  └─> level_strategy
                                                       └─> company_comp_research
                                                            └─> personalization_plan
                                                                 └─> interview_prep
                                                                      └─> score_and_recommend
                                                                           └─> draft_application_answers  (no-op if score < 4.5)
                                                                                └─> update_story_bank
                                                                                    │
                                                    ┌─(generate_pdf)─────────────────┘─(skip)──┐
                                                tailor_cv                            skip_pdf  │
                                                     └─> generate_cv_html_pdf                  │
                                                          └──────────────┬───────────────────────┘
                                                                generate_cover_letter  (no-op unless flag)
                                                                         └─> write_report
                                                                              └─> write_tracker_addition
                                                                                    └─> merge_or_update_tracker
                                                                                            └─> END
"""

from __future__ import annotations

from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from job_hunt.models.state import JobHuntState
from job_hunt.nodes.classify import classify_archetype
from job_hunt.nodes.context import load_context
from job_hunt.nodes.cover_letter import generate_cover_letter
from job_hunt.nodes.eligibility_gate import eligibility_gate, mark_ineligible
from job_hunt.nodes.evaluate import cv_match, level_strategy, role_summary, score_and_recommend
from job_hunt.nodes.extract import extract_jd, mark_unavailable, verify_active
from job_hunt.nodes.pdf import generate_cv_html_pdf, skip_pdf, tailor_cv
from job_hunt.nodes.personalize import (
    draft_application_answers,
    interview_prep,
    personalization_plan,
    update_story_bank,
)
from job_hunt.nodes.report import write_report
from job_hunt.nodes.research import company_comp_research
from job_hunt.nodes.tracker import merge_or_update_tracker, write_tracker_addition


def _route_active(state: JobHuntState) -> Literal["eligibility_gate", "mark_unavailable"]:
    return "eligibility_gate" if state.get("jd_active", True) else "mark_unavailable"


def _route_eligibility(state: JobHuntState) -> Literal["classify_archetype", "mark_ineligible"]:
    """Block when the JD eligibility class disagrees with the active mode.

    Unknown classification passes through — see docs/design-notes.md §N.3.
    """
    mode = state.get("mode", "full")
    classification = state.get("jd_eligibility", "unknown")
    if classification == "unknown" or classification == mode:
        return "classify_archetype"
    return "mark_ineligible"


def _route_pdf(state: JobHuntState) -> Literal["tailor_cv", "skip_pdf"]:
    scores = state.get("scores")
    return "tailor_cv" if (scores and scores.generate_pdf) else "skip_pdf"


def build_evaluate_job_graph():
    builder = StateGraph(JobHuntState)

    # --- nodes ---
    builder.add_node("load_context", load_context)
    builder.add_node("extract_jd", extract_jd)
    builder.add_node("verify_active", verify_active)
    builder.add_node("mark_unavailable", mark_unavailable)
    builder.add_node("eligibility_gate", eligibility_gate)
    builder.add_node("mark_ineligible", mark_ineligible)
    builder.add_node("classify_archetype", classify_archetype)

    # analysis nodes. They are intentionally sequential in the first release so
    # local proxies and low-cost models are not overloaded by concurrent calls.
    builder.add_node("cv_match", cv_match)
    builder.add_node("role_summary", role_summary)
    builder.add_node("level_strategy", level_strategy)
    builder.add_node("company_comp_research", company_comp_research)

    # synthesis (depends on phase-1 results via state)
    builder.add_node("personalization_plan", personalization_plan)
    builder.add_node("interview_prep", interview_prep)

    builder.add_node("score_and_recommend", score_and_recommend)
    builder.add_node("draft_application_answers", draft_application_answers)
    builder.add_node("update_story_bank", update_story_bank)
    builder.add_node("tailor_cv", tailor_cv)
    builder.add_node("generate_cv_html_pdf", generate_cv_html_pdf)
    builder.add_node("skip_pdf", skip_pdf)
    builder.add_node("generate_cover_letter", generate_cover_letter)
    builder.add_node("write_report", write_report)
    builder.add_node("write_tracker_addition", write_tracker_addition)
    builder.add_node("merge_or_update_tracker", merge_or_update_tracker)

    # --- edges ---
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "extract_jd")
    builder.add_edge("extract_jd", "verify_active")
    builder.add_conditional_edges("verify_active", _route_active)
    builder.add_edge("mark_unavailable", END)
    builder.add_conditional_edges("eligibility_gate", _route_eligibility)
    builder.add_edge("mark_ineligible", END)

    builder.add_edge("classify_archetype", "cv_match")
    builder.add_edge("cv_match", "role_summary")
    builder.add_edge("role_summary", "level_strategy")
    builder.add_edge("level_strategy", "company_comp_research")
    builder.add_edge("company_comp_research", "personalization_plan")
    builder.add_edge("personalization_plan", "interview_prep")
    builder.add_edge("interview_prep", "score_and_recommend")
    builder.add_edge("score_and_recommend", "draft_application_answers")
    builder.add_edge("draft_application_answers", "update_story_bank")

    builder.add_conditional_edges("update_story_bank", _route_pdf)
    builder.add_edge("tailor_cv", "generate_cv_html_pdf")
    builder.add_edge("generate_cv_html_pdf", "generate_cover_letter")
    builder.add_edge("skip_pdf", "generate_cover_letter")
    builder.add_edge("generate_cover_letter", "write_report")
    builder.add_edge("write_report", "write_tracker_addition")
    builder.add_edge("write_tracker_addition", "merge_or_update_tracker")
    builder.add_edge("merge_or_update_tracker", END)

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
