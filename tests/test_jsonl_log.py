from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from job_hunt.repositories.jsonl_log import JsonlLog


class Widget(BaseModel):
    id: str
    status: Literal["ok", "broken"] = "ok"


GOOD = '{"id":"w_good","status":"ok"}'
# The shape that bricked every inbound command: a value outside the schema.
BAD = '{"id":"w_bad","status":"not_a_real_status"}'


def log_with(tmp_path, *lines) -> JsonlLog[Widget]:
    path = tmp_path / "widgets.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return JsonlLog(path, Widget)


def test_one_bad_row_does_not_hide_the_good_ones(tmp_path):
    log = log_with(tmp_path, GOOD, BAD, GOOD.replace("w_good", "w_good2"))
    assert [w.id for w in log.list(limit=100)] == ["w_good", "w_good2"]


def test_malformed_rows_are_reported_with_their_line_number(tmp_path):
    log = log_with(tmp_path, GOOD, BAD)
    malformed = log.malformed()
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert "w_bad" in malformed[0].raw


def test_healthy_file_reports_nothing_malformed(tmp_path):
    log = log_with(tmp_path, GOOD)
    assert log.malformed() == []


def test_missing_file_reads_as_empty(tmp_path):
    log = JsonlLog(tmp_path / "missing.jsonl", Widget)
    assert log.read() == ([], [])
    assert log.list() == []
    assert log.malformed() == []


def test_append_then_read_round_trips(tmp_path):
    log = JsonlLog(tmp_path / "widgets.jsonl", Widget)
    log.append(Widget(id="w1"))
    log.append(Widget(id="w2"))
    assert [w.id for w in log.list()] == ["w1", "w2"]


def test_list_truncates_to_limit(tmp_path):
    log = JsonlLog(tmp_path / "widgets.jsonl", Widget)
    for i in range(5):
        log.append(Widget(id=f"w{i}"))
    assert [w.id for w in log.list(limit=2)] == ["w3", "w4"]


def test_replace_line_repairs_a_malformed_row(tmp_path):
    log = log_with(tmp_path, GOOD, BAD)
    log.replace_line(2, Widget(id="w_bad", status="broken"))
    assert log.malformed() == []
    assert [w.id for w in log.list(limit=100)] == ["w_good", "w_bad"]


def test_write_all_preserves_malformed_lines_at_the_end(tmp_path):
    log = log_with(tmp_path, GOOD, BAD)
    records, malformed = log.read()
    records.append(Widget(id="w_new"))
    log.write_all(records, malformed)
    assert [w.id for w in log.list(limit=100)] == ["w_good", "w_new"]
    remaining = log.malformed()
    assert len(remaining) == 1
    assert "w_bad" in remaining[0].raw
