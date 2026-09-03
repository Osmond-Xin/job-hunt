from __future__ import annotations

from typing import Literal
from unittest.mock import patch

import filelock
import pytest
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


def test_interrupted_write_leaves_original_file_intact(tmp_path):
    """write_all() writes to a temp file and os.replace()s it into place. If
    the replace step itself is interrupted (crash, kill signal) after the
    temp file exists but before it lands, the original file must be
    untouched rather than truncated or half-written."""
    log = log_with(tmp_path, GOOD)
    original = log.path.read_text(encoding="utf-8")

    with patch("job_hunt.repositories.jsonl_log.os.replace", side_effect=OSError("simulated crash")):
        with pytest.raises(OSError, match="simulated crash"):
            log.write_all([Widget(id="w_new")])

    assert log.path.read_text(encoding="utf-8") == original
    # The abandoned temp file should not be left behind either.
    assert list(tmp_path.glob("*.tmp")) == []


def test_two_sequential_write_all_cycles_do_not_lose_the_first_record(tmp_path):
    """Two back-to-back read-modify-write cycles through the same JsonlLog
    must each see the other's prior write, not clobber it."""
    log = JsonlLog(tmp_path / "widgets.jsonl", Widget)

    records, malformed = log.read()
    records.append(Widget(id="w1"))
    log.write_all(records, malformed)

    records, malformed = log.read()
    records.append(Widget(id="w2"))
    log.write_all(records, malformed)

    assert [w.id for w in log.list(limit=100)] == ["w1", "w2"]


def test_replace_line_out_of_range_raises_indexerror_instead_of_corrupting(tmp_path):
    """A stale line_number past the end of the file must raise, not silently
    write past the file or corrupt an unrelated line."""
    log = log_with(tmp_path, GOOD, BAD)
    with pytest.raises(IndexError):
        log.replace_line(5, Widget(id="w_new"))
    # Nothing was touched.
    assert log.path.read_text(encoding="utf-8") == "\n".join([GOOD, BAD]) + "\n"


def test_replace_line_zero_or_negative_raises_instead_of_wrapping_around(tmp_path):
    """line_number 0 or negative would, without an explicit bounds check,
    land on Python's negative-index wraparound and silently overwrite the
    wrong (unrelated) line from the end of the file. It must raise instead."""
    log = log_with(tmp_path, GOOD, BAD)
    before = log.path.read_text(encoding="utf-8")

    with pytest.raises(IndexError):
        log.replace_line(0, Widget(id="w_new"))
    with pytest.raises(IndexError):
        log.replace_line(-1, Widget(id="w_new"))

    assert log.path.read_text(encoding="utf-8") == before


def test_lock_is_held_across_the_whole_replace_line_read_and_write(tmp_path):
    """replace_line() must hold the lock for its entire read-modify-write, not
    just around the final write, so a concurrent locked writer can't slip a
    change in between the read and the write. Verified by probing, from
    inside the read step, whether an independent lock on the same file can
    be acquired -- it must not be able to."""
    log = log_with(tmp_path, GOOD, BAD)
    lock_path = str(log.path) + ".lock"
    observed: list[bool] = []

    real_read_text = type(log.path).read_text

    def spy_read_text(self, *args, **kwargs):
        if str(self) == str(log.path):
            probe = filelock.FileLock(lock_path, timeout=0)
            try:
                probe.acquire()
                observed.append(True)  # lock was NOT held -- would be a bug
                probe.release()
            except filelock.Timeout:
                observed.append(False)  # lock was held throughout -- correct
        return real_read_text(self, *args, **kwargs)

    with patch("pathlib.Path.read_text", spy_read_text):
        log.replace_line(2, Widget(id="w_bad", status="broken"))

    assert observed == [False]
