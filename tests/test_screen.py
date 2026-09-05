"""The MiniMax screening pass.

The property that matters is fail-open: a screening outage, a truncated
response, or a row the model skipped must widen the shortlist, never silently
shorten it. Losing a posting is the expensive error.
"""

from __future__ import annotations

from job_hunt.services.screen import Screened, build_prompt, parse_response, screen

ROWS = [
    ("Government of Manitoba", "Data Engineer", "Winnipeg MB"),
    ("Randstad", "AI Engineer", "Halifax, NS"),
    ("BigCo", "VP of Sales", "Toronto, ON"),
]


def test_prompt_numbers_every_row_and_carries_the_drop_rules():
    prompt = build_prompt(ROWS)
    assert "1. Data Engineer — Government of Manitoba — Winnipeg MB" in prompt
    assert "3. VP of Sales" in prompt
    assert "Staffing agencies" in prompt
    assert "location not stated" not in prompt


def test_missing_location_is_labelled_rather_than_left_blank():
    assert "location not stated" in build_prompt([("Co", "AI Engineer", "")])


def test_parses_the_expected_line_shape():
    text = "1 | KEEP | 4 | public sector data role\n2 | DROP | 1 | staffing agency\n"
    out = parse_response(text, 3)
    assert out[1].keep is True and out[1].fit == 4.0
    assert out[2].keep is False
    assert out[2].reason == "staffing agency"
    assert 3 not in out


def test_out_of_range_and_repeated_indices_are_ignored():
    text = "1 | KEEP | 5 | first wins\n1 | DROP | 0 | duplicate\n9 | DROP | 0 | out of range\n"
    out = parse_response(text, 3)
    assert out[1].reason == "first wins"
    assert 9 not in out


def test_a_row_the_model_skipped_is_kept_and_flagged():
    def runner(_prompt, _model, _tokens, _timeout):
        return "1 | KEEP | 4 | fine\n", ""

    verdicts, error = screen(ROWS, runner=runner)
    assert error == ""
    assert verdicts[1].screened is True
    for index in (2, 3):
        assert verdicts[index].keep is True
        assert verdicts[index].screened is False
        assert verdicts[index].reason == "not screened"


def test_a_screening_outage_keeps_everything():
    def runner(_prompt, _model, _tokens, _timeout):
        return "", "mmx not on PATH"

    verdicts, error = screen(ROWS, runner=runner)
    assert error == "mmx not on PATH"
    assert all(v.keep and not v.screened for v in verdicts.values())


def test_an_exception_in_the_runner_is_not_fatal():
    def runner(*_args):
        raise RuntimeError("boom")

    verdicts, error = screen(ROWS, runner=runner)
    assert "boom" in error
    assert len(verdicts) == len(ROWS)
    assert all(v.keep for v in verdicts.values())


def test_no_rows_is_a_no_op():
    assert screen([]) == ({}, "")
