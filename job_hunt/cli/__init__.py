from __future__ import annotations

import shutil
import typer

app = typer.Typer(help="Job Hunt operations CLI.")
config_app = typer.Typer(help="Configuration commands.")
trace_app = typer.Typer(help="LangSmith tracing commands.")
activity_app = typer.Typer(help="Activity log commands.")
tracker_app = typer.Typer(help="Tracker commands.")
schedule_app = typer.Typer(help="Scheduler commands.")
email_app = typer.Typer(help="Gmail ingestion commands.")
llm_app = typer.Typer(help="LLM provider commands.")
review_app = typer.Typer(help="Review queue commands.")
contacts_app = typer.Typer(help="Contact CRM commands.")
outreach_app = typer.Typer(help="Outreach tracking commands.")

app.add_typer(config_app, name="config")
app.add_typer(trace_app, name="trace")
app.add_typer(activity_app, name="activity")
app.add_typer(tracker_app, name="tracker")
app.add_typer(schedule_app, name="schedule")
app.add_typer(email_app, name="email")
app.add_typer(llm_app, name="llm")
app.add_typer(review_app, name="review")
app.add_typer(contacts_app, name="contacts")
app.add_typer(outreach_app, name="outreach")

pipeline_app = typer.Typer(help="Pipeline.md URL inbox commands.")
app.add_typer(pipeline_app, name="pipeline")


# Importing each command module registers its commands on `app` (or a
# sub-app) as a side effect of the @app.command()/@x_app.command()
# decorators below running at import time. The names below are re-exported
# so `job_hunt.cli.<name>` keeps resolving exactly as it did when this was
# a single module - tests import commands and private helpers directly off
# `job_hunt.cli`, and some monkeypatch them there (see cli/evaluation.py
# and cli/apply.py for how those call sites read the patched value back
# through this module rather than through a bare/static-imported name).
from ._render import (
    console,
    _short,
)
from .setup import (
    ONBOARDING_NEXT_STEPS,
    default_gcloud_adc_path,
    _copy_setup_examples,
    _write_onboarding_profile,
    _update_env_file,
    _import_resume_file,
    _extract_pdf_text,
    onboarding_init,
    configure_ai,
    import_resume,
    config_validate,
    config_doctor,
    config_set_mode,
    config_init,
)
from .discovery import (
    _shortlist_rows,
    search_jobs,
    shortlist,
    _IMMIGRATION_LANE_SLOTS,
    _immigration_lane,
    triage,
    scan,
)
from .evaluation import (
    evaluate,
    _BATCH_MAX_CONCURRENCY,
    evaluate_batch,
    _batch_preflight,
    _partition_already_evaluated,
    _strip_aggregator_suffix,
    _ledger_path,
    _ledger_line_count,
    _ledger_cost_since,
    _ledger_spend_since,
    _resolve_source_type,
    build_evaluate_job_graph,
    load_settings,
    LLM_FAILURE_MARKER,
)
from .tracking import (
    tracker_init,
    tracker_stats,
    tracker_verify,
    tracker_merge,
    tracker_dedup,
    tracker_normalize,
    pipeline_add,
    pipeline_list,
    pipeline_process,
    tracker_dashboard,
    tracker_check_sync,
    compare_offers,
    checkup,
)
from .mail import (
    email_poll,
    email_summarize,
    _warn_malformed_events,
    email_verify,
    email_gaps,
    email_events,
    email_parse_sample,
    email_import_events,
    email_reconcile,
    email_review_candidates,
    email_approve_event,
    email_ignore_event,
    review_list,
)
from .outreach import (
    contacts_add,
    contacts_search,
    _gate_outward_artifact,
    outreach_draft,
    outreach_log,
    outreach_mark_sent,
    outreach_mark,
    outreach_due,
    _load_cv_excerpt,
    _build_research_context_for_prompt,
    _run_one_shot_prompt,
    linkedin_outreach,
    research_prompt,
    project_eval,
    training_eval,
)
from .diagnostics import (
    trace_status,
    trace_on,
    trace_off,
    trace_smoke_test,
    activity_list,
    activity_tail,
    activity_slack_test,
    schedule_list,
    schedule_run,
    llm_cheap_test,
    search_test,
    proxy_check,
    search_usage,
)
from .apply import (
    apply_assist,
    agent_apply_prompt,
    full_loop_from_url,
    apply_replace_pdf,
    apply_capture_page,
    apply_refill_current_page,
    apply_answers,
    apply_close_session,
    apply_status,
    apply_do,
    _SUBMIT_LABEL_RE,
    _looks_like_submit_label,
    ApplyDoRefused,
    _active_apply_artifact_dir,
    _build_agent_apply_prompt,
    _infer_loop_target,
    _extract_loop_url_metadata,
    _parse_company_role_from_description,
    _company_from_apply_url,
    _best_tracker_text_match,
    _select_pdf_for_entry,
    _select_pdf_for_text,
    _tracker_entry_blocks_apply,
    _loop_agent_apply_command,
    _BROWSER_PROFILE,
    _CDP_PORT,
    _SCREENSHOT_JPEG_QUALITY,
    _save_session_screenshot,
    _open_apply_page,
    _handle_refill_current_page,
    _collect_status_payload,
    _handle_do_command,
    _resolve_unique_target,
    _element_looks_like_submit,
    _do_click_by_label,
    _do_fill_by_label,
    _do_select_by_label,
    _do_check_by_label,
    _enter_application_form,
    _advance_application_start,
    _maybe_workday_login,
    _recover_workday_error_page,
    _try_workday_final_submit,
    _workday_resume_was_uploaded,
    _wait_for_application_ready,
    _attach_resume,
    _COVER_LETTER_LABEL_RE,
    _attach_cover_letter,
    _finish_pending_upload_dialog,
    _auto_fill_application,
    _fill_workday_current_step,
    _workday_current_step,
    _workday_required_blocks_my_information_continue,
    _select_workday_dropdown_by_label,
    _select_workday_dropdown_containing_label,
    _select_workday_dropdown_in_question,
    _choose_workday_option,
    _click_workday_save_and_continue,
    _WORKDAY_MAX_STEPS,
    _WORKDAY_STEP_CHANGE_TIMEOUT_MS,
    _workday_advance_all_steps,
    _workday_review_needs_repair,
    _collect_workday_review_issues,
    _workday_go_back_to_step,
    _fill_workday_my_experience,
    _write_workday_my_experience_debug,
    _ensure_workday_section_item,
    _fill_workday_structured_experience,
    _force_fill_by_accessible_label,
    _fill_workday_experience_card_by_order,
    _fill_workday_experience_dates_by_title,
    _replace_workday_element_value,
    _workday_experience_dates_match,
    _fill_workday_structured_education,
    _fill_workday_education_card_by_order,
    _fill_workday_social_network_url,
    _fill_workday_scoped_field,
    _workday_any_input_has_value,
    _workday_experience_entries,
    _workday_education_entries,
    _workday_remove_duplicate_uploads,
    _fill_workday_voluntary_disclosures,
    _wait_for_workday_step_change,
    _run_workday_question_ops,
    _fill_workday_application_questions,
    _fill_workday_textarea_answers,
    _select_workday_dropdown_by_index,
    _fill_workday_date_input,
    _fill_workday_input_in_question,
    _workday_element_in_question,
    _fill_workday_field_containing,
    _scroll_application_form,
    _fill_by_label_or_placeholder,
    _fill_contenteditable,
    _field_contains_text,
    _fill_by_visible_label,
    _looks_like_honeypot_context,
    _fill_location,
    _page_identity_warnings,
    _linkedin_modal,
    _linkedin_click_by_name,
    _linkedin_fill_by_label,
    _linkedin_select_dropdown,
    _linkedin_dropdown_options,
    _linkedin_select_radio,
    _linkedin_attach_resume,
    _linkedin_read_modal_heading,
    _linkedin_read_required_empty,
    _linkedin_read_modal_fields,
    _maybe_linkedin_easy_apply,
    _required_empty_fields,
    _field_context,
    _click_radio_near_text,
    _apply_artifact_dir,
    _apply_profile_values,
    _LOW_SCORE_GATE_THRESHOLD,
    APPLY_REVIEW_SCHEMA_VERSION,
    _enforce_low_score_gate,
    _load_apply_report_context,
    _resolve_report_path,
    _extract_application_section,
    _extract_report_recommendation,
    _report_fit_warnings,
    _find_report_answer,
    _find_saved_apply_answer,
    _normalize_question,
    _load_saved_apply_answers,
    _section_blocks_matching,
    _clean_report_answer,
    _answer_for_application_question,
    _radio_choice_for_question,
    _write_apply_review_summary,
    _append_apply_review_event,
    _tracker_entry_by_id,
    _link_artifacts_to_row,
    _record_manual_submission,
    _filter_required_empty_fields,
)

if __name__ == "__main__":
    app()
