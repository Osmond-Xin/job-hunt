"""`job-hunt loop --evaluate` must resolve a real source_type before invoking the graph.

Regression for: the loop path called graph.ainvoke() without source_type, so
nodes/extract.py defaulted to "jd_text" and treated the URL string itself as the
JD body. verify_active then saw len < 200 and every run short-circuited to
mark_unavailable — `loop --evaluate` could never actually evaluate anything.
"""

from __future__ import annotations

from typer.testing import CliRunner

from job_hunt import cli
from job_hunt.cli import app

runner = CliRunner()


class _FakeGraph:
    """Records the state the loop path invoked it with."""

    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return {"errors": []}


def test_loop_evaluate_passes_a_resolved_source_type(monkeypatch) -> None:
    graph = _FakeGraph()
    # full_loop_from_url (cli.apply) calls all three of these as bare names, so
    # the patches have to land on cli.apply's own copies, not job_hunt.cli's
    # re-export — that only rebinds this module's attribute.
    monkeypatch.setattr(cli.apply, "build_evaluate_job_graph", lambda: graph)
    # Keep the rest of `loop` from doing real inference/network work; the fix
    # under test is entirely in the graph.ainvoke() call before this point.
    monkeypatch.setattr(
        cli.apply,
        "_infer_loop_target",
        lambda *, url, description: {
            "company": None,
            "role": None,
            "pdf": None,
            "metadata": None,
            "tracker_entry": None,
        },
    )
    monkeypatch.setattr(
        cli.apply, "_loop_agent_apply_command", lambda *, url, company, role, pdf: "echo noop"
    )

    result = runner.invoke(
        app, ["loop", "https://example.com/jobs/123", "--evaluate", "--no-prompt"]
    )

    assert result.exit_code == 0, result.output
    assert len(graph.states) == 1
    state = graph.states[0]
    assert state["source_type"] == "url"
    assert state["url"] == "https://example.com/jobs/123"
