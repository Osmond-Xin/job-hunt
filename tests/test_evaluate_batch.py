"""`job-hunt evaluate-batch` — target parsing and failure isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from job_hunt import cli
from job_hunt.cli import app
from job_hunt.services import batch, usage_ledger

runner = CliRunner()

# Captured before the autouse fixture stubs it out for the CLI-level tests.
_REAL_PREFLIGHT = cli._batch_preflight
_REAL_SPEND_SINCE = cli._ledger_spend_since


class _FakeGraph:
    """Records the state each job was invoked with; fails on a marked target."""

    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        if "boom" in state["input"]:
            raise RuntimeError("extraction blew up")
        from job_hunt.models.evaluation import EvaluationScores
        from job_hunt.models.job import JobMeta

        return {
            "jd_meta": JobMeta(company=f"Co-{state['input'][-1]}", title="Engineer"),
            "scores": EvaluationScores(weighted_total=4.2),
            "recommendation": "apply",
            "report_path": "reports/x.md",
            "errors": [],
        }


def _write_targets(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "urls.txt"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _hermetic_batch(monkeypatch):
    """Skip the real preflight and tracker lookups; those have their own tests."""
    monkeypatch.setattr(cli.evaluation, "_batch_preflight", lambda budget_enforced=False: None)
    monkeypatch.setattr(cli.evaluation, "_partition_already_evaluated", lambda targets: (targets, []))
    # These now run inside run_batch (job_hunt.services.batch), which imports
    # them from usage_ledger by name — patching cli.evaluation's copy would no
    # longer reach the call site.
    monkeypatch.setattr(batch, "_ledger_line_count", lambda: 0)
    monkeypatch.setattr(batch, "_ledger_spend_since", lambda start: (0.0, 0, 0))


def test_batch_skips_comments_and_blanks(tmp_path, monkeypatch) -> None:
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    targets = _write_targets(
        tmp_path,
        "# a comment\n\nhttps://example.com/1\n   \nhttps://example.com/2\n# trailing\n",
    )
    result = runner.invoke(app, ["evaluate-batch", str(targets)])
    assert result.exit_code == 0, result.output
    assert [state["input"] for state in graph.states] == [
        "https://example.com/1",
        "https://example.com/2",
    ]


def test_batch_isolates_a_failing_job(tmp_path, monkeypatch) -> None:
    """One unparseable JD must not take the other jobs down with it."""
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    targets = _write_targets(
        tmp_path, "https://example.com/boom\nhttps://example.com/1\n"
    )
    result = runner.invoke(app, ["evaluate-batch", str(targets)])
    # The healthy job still ran and is reported...
    assert "FAILED" in result.output
    assert "Co-1" in result.output
    # ...but a failed job must not look like a clean run to a caller/script.
    assert result.exit_code == 1

    forgiving = runner.invoke(app, ["evaluate-batch", str(targets), "--max-failures", "1"])
    assert forgiving.exit_code == 0, forgiving.output


def test_batch_rejects_an_empty_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: _FakeGraph())
    targets = _write_targets(tmp_path, "# only comments\n")
    result = runner.invoke(app, ["evaluate-batch", str(targets)])
    assert result.exit_code == 1
    assert "No targets found" in result.output


def test_batch_deduplicates_repeated_targets(tmp_path, monkeypatch) -> None:
    """The same URL twice costs twice and the second result overwrites the first."""
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    targets = _write_targets(
        tmp_path, "https://example.com/1\nhttps://example.com/1\nhttps://example.com/2\n"
    )
    result = runner.invoke(app, ["evaluate-batch", str(targets)])
    assert result.exit_code == 0, result.output
    assert len(graph.states) == 2


def test_batch_refuses_more_targets_than_max_jobs(tmp_path, monkeypatch) -> None:
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    targets = _write_targets(
        tmp_path, "\n".join(f"https://example.com/{n}" for n in range(5)) + "\n"
    )
    result = runner.invoke(app, ["evaluate-batch", str(targets), "--max-jobs", "3"])
    assert result.exit_code == 1
    assert "exceeds --max-jobs" in result.output
    assert graph.states == []  # nothing was spent


def test_batch_clamps_runaway_concurrency(tmp_path, monkeypatch) -> None:
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    targets = _write_targets(tmp_path, "https://example.com/1\n")
    result = runner.invoke(app, ["evaluate-batch", str(targets), "--concurrency", "50"])
    assert result.exit_code == 0, result.output
    assert "Clamping" in result.output


def test_batch_stops_launching_jobs_over_budget(tmp_path, monkeypatch) -> None:
    """A runaway spend must stop the batch, not just be reported afterwards."""
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    monkeypatch.setattr(batch, "_ledger_spend_since", lambda start: (99.0, 3, 3))
    targets = _write_targets(
        tmp_path, "\n".join(f"https://example.com/{n}" for n in range(4)) + "\n"
    )
    result = runner.invoke(
        app, ["evaluate-batch", str(targets), "--concurrency", "1", "--max-cost", "1.0"]
    )
    assert result.exit_code == 0, result.output
    # First job runs, then the cap trips and the rest are never started.
    assert len(graph.states) == 1
    assert "OVER BUDGET" in result.output
    assert "Budget cap" in result.output


def test_batch_surfaces_withheld_artifacts(tmp_path, monkeypatch) -> None:
    class _WarningGraph(_FakeGraph):
        async def ainvoke(self, state, config=None):
            result = await super().ainvoke(state, config)
            result["artifact_warnings"] = ["cover letter withheld (audit failed): too long"]
            return result

    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: _WarningGraph())
    targets = _write_targets(tmp_path, "https://example.com/1\n")
    result = runner.invoke(app, ["evaluate-batch", str(targets)])
    assert result.exit_code == 0, result.output
    assert "must review before sending" in result.output
    assert "withheld" in result.output


def test_batch_counts_provider_outages_as_failures(tmp_path, monkeypatch) -> None:
    """A job that completed only because every LLM call fell back is not a success.

    Without this, a broken provider produces 50 "evaluated" jobs full of
    placeholder text, tracker rows written as if real, and exit code 0.
    """

    class _DegradedGraph(_FakeGraph):
        async def ainvoke(self, state, config=None):
            result = await super().ainvoke(state, config)
            result["errors"] = [f"cv_match {cli.LLM_FAILURE_MARKER}; using fallback content: X"]
            return result

    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: _DegradedGraph())
    targets = _write_targets(tmp_path, "https://example.com/1\n")
    result = runner.invoke(app, ["evaluate-batch", str(targets)])
    assert "ran on fallback content" in result.output
    assert result.exit_code == 1


def test_batch_rejects_a_budget_it_cannot_measure(tmp_path, monkeypatch) -> None:
    """--max-cost is only meaningful if cost is actually recorded."""
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: _FakeGraph())
    monkeypatch.undo()  # restore the real preflight for this check
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: _FakeGraph())

    class _Ledger:
        enabled = False

    class _Obs:
        local_ledger = _Ledger()

    class _Premium:
        command = ["claude", "-p"]

    class _Llm:
        premium = _Premium()

    class _Settings:
        observability = _Obs()
        llm = _Llm()

    monkeypatch.setattr(cli.evaluation, "load_settings", lambda: _Settings())
    monkeypatch.setenv("JOB_HUNT_SKIP_CV_SYNC_CHECK", "1")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")
    targets = _write_targets(tmp_path, "https://example.com/1\n")
    result = runner.invoke(app, ["evaluate-batch", str(targets), "--max-cost", "5"])
    assert result.exit_code == 1
    assert "needs the local ledger" in result.output


def test_batch_rejects_negative_budget(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: _FakeGraph())
    targets = _write_targets(tmp_path, "https://example.com/1\n")
    result = runner.invoke(app, ["evaluate-batch", str(targets), "--max-cost", "-1"])
    assert result.exit_code == 1
    assert "cannot be negative" in result.output


def _stub_settings(*, ledger_enabled: bool = True, command: list[str] | None = None):
    class _Ledger:
        enabled = ledger_enabled

    class _Obs:
        local_ledger = _Ledger()

    class _Premium:
        pass

    _Premium.command = ["claude", "-p", "--output-format", "json"] if command is None else command

    class _Llm:
        premium = _Premium()

    class _Paths:
        data_dir = "data"

    class _Settings:
        observability = _Obs()
        llm = _Llm()
        paths = _Paths()

    return _Settings()


def test_preflight_rejects_an_empty_premium_command(monkeypatch) -> None:
    monkeypatch.setattr(cli.evaluation, "load_settings", lambda: _stub_settings(command=[]))
    monkeypatch.setenv("JOB_HUNT_SKIP_CV_SYNC_CHECK", "1")
    with pytest.raises(typer.Exit):
        _REAL_PREFLIGHT()


def test_budget_preflight_uses_the_providers_own_json_detection(monkeypatch) -> None:
    """A naive substring check both false-accepted and false-rejected.

    `["claude", "-p", "json"]` records no cost but contains "json"; the
    supported `--output-format=json` spelling contains no bare "json" token.
    """
    monkeypatch.setenv("JOB_HUNT_SKIP_CV_SYNC_CHECK", "1")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")

    monkeypatch.setattr(
        cli.evaluation, "load_settings", lambda: _stub_settings(command=["claude", "-p", "json"])
    )
    with pytest.raises(typer.Exit):
        _REAL_PREFLIGHT(budget_enforced=True)

    monkeypatch.setattr(
        cli.evaluation,
        "load_settings",
        lambda: _stub_settings(command=["claude", "-p", "--output-format=json"]),
    )
    _REAL_PREFLIGHT(budget_enforced=True)  # supported spelling must be accepted


def test_budget_preflight_requires_the_ledger(monkeypatch) -> None:
    monkeypatch.setenv("JOB_HUNT_SKIP_CV_SYNC_CHECK", "1")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(cli.evaluation, "load_settings", lambda: _stub_settings(ledger_enabled=False))
    with pytest.raises(typer.Exit):
        _REAL_PREFLIGHT(budget_enforced=True)


def test_batch_stops_when_spend_is_not_being_recorded(tmp_path, monkeypatch) -> None:
    """Premium calls with no cost recorded make --max-cost read $0.00 forever.

    That is indistinguishable from "nothing spent" to a plain sum, so the cap
    would silently let the whole list run.
    """
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    # Premium calls happened (3 records) but none carried a cost.
    monkeypatch.setattr(batch, "_ledger_spend_since", lambda start: (0.0, 3, 0))
    targets = _write_targets(
        tmp_path, "\n".join(f"https://example.com/{n}" for n in range(4)) + "\n"
    )
    result = runner.invoke(
        app, ["evaluate-batch", str(targets), "--concurrency", "1", "--max-cost", "5.0"]
    )
    assert len(graph.states) == 1  # stopped after the first job
    assert "recorded no cost" in result.output


def test_ledger_spend_only_counts_premium_records(tmp_path, monkeypatch) -> None:
    """Cheap-tier rows carry no USD cost and must not dilute the counters."""
    import json as _json

    ledger = tmp_path / "usage-ledger.jsonl"
    ledger.write_text(
        "\n".join(
            _json.dumps(row)
            for row in [
                {"model_tier": "cheap", "cost_usd": None},
                {"model_tier": "premium", "cost_usd": 0.03},
                {"model_tier": "premium", "cost_usd": None},
                # bool is an int subclass; NaN/Infinity survive a JSON round
                # trip. None of these are a usable cost.
                {"model_tier": "premium", "cost_usd": True},
                {"model_tier": "premium", "cost_usd": float("nan")},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # `_ledger_spend_since` calls `_ledger_path()` as a bare name, which
    # resolves against usage_ledger's own module namespace (where it is
    # defined), not cli.evaluation's re-exported copy.
    monkeypatch.setattr(usage_ledger, "_ledger_path", lambda: ledger)
    total, premium, priced = _REAL_SPEND_SINCE(0)
    assert (round(total, 4), premium, priced) == (0.03, 4, 1)


def test_batch_stops_when_only_some_premium_calls_report_cost(tmp_path, monkeypatch) -> None:
    """Partial cost data undercounts spend, so the cap trips late or never.

    Two premium calls, one priced: the running total says $0.03 while real
    spend is higher. Incomplete measurement is treated like no measurement.
    """
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    monkeypatch.setattr(batch, "_ledger_spend_since", lambda start: (0.03, 2, 1))
    targets = _write_targets(
        tmp_path, "\n".join(f"https://example.com/{n}" for n in range(4)) + "\n"
    )
    result = runner.invoke(
        app, ["evaluate-batch", str(targets), "--concurrency", "1", "--max-cost", "5.0"]
    )
    assert len(graph.states) == 1
    assert "recorded no cost" in result.output


def test_batch_runs_on_when_every_premium_call_is_priced(tmp_path, monkeypatch) -> None:
    """Complete, under-budget cost data must not trip the guard."""
    graph = _FakeGraph()
    monkeypatch.setattr(batch, "build_evaluate_job_graph", lambda: graph)
    monkeypatch.setattr(batch, "_ledger_spend_since", lambda start: (0.06, 2, 2))
    targets = _write_targets(
        tmp_path, "\n".join(f"https://example.com/{n}" for n in range(3)) + "\n"
    )
    result = runner.invoke(
        app, ["evaluate-batch", str(targets), "--concurrency", "1", "--max-cost", "5.0"]
    )
    assert result.exit_code == 0, result.output
    assert len(graph.states) == 3
    assert "recorded no cost" not in result.output
