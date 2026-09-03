"""CLAUDE.md §1 gate for the two CLI paths that used to skip it entirely.

`nodes/redteam.py` covers `pdf_path` / `cover_letter_path` on every pipeline
run, but `outreach draft` and `apply-answers` build outward-facing text —
a message to a real contact, ready-to-paste application-form answers — and
printed it straight to the console with no review at all. Both now route
through the same `run_review` used by the node and `scripts/redteam.py`, via
`job_hunt.cli._gate_outward_artifact`. As with `test_redteam_gate.py`, the
asymmetry under test is that a reviewer that could not be reached is
UNREVIEWED, never a pass.
"""

from __future__ import annotations

from pathlib import Path

from job_hunt.cli import apply_answers, linkedin_outreach, outreach_draft
from job_hunt.nodes.apply_screen_assist import ApplyAnswersResult
from job_hunt.services import redteam as svc
from job_hunt.services.outreach import Contact, add_contact


def _fake_run_review(verdict: str, review: str = "", errors: list[str] | None = None):
    def _run(*, artifacts, jd_text, company, role, **kwargs):
        return svc.RedTeamResult(verdict, review, errors or [])

    return _run


def _recording_run_review(verdict: str, review: str, calls: list[dict]):
    """Like `_fake_run_review`, but records the kwargs each call received so a
    test can assert on what `jd_text` the reviewer actually saw."""

    def _run(*, artifacts, jd_text, company, role, **kwargs):
        calls.append({"artifacts": artifacts, "jd_text": jd_text, "company": company, "role": role})
        return svc.RedTeamResult(verdict, review, [])

    return _run


def _fake_generate_apply_answers(content: str):
    async def _gen(**kwargs):
        return ApplyAnswersResult(content=content, errors=[])

    return _gen


# ----- outreach draft -----


def _make_contact(tmp_path: Path, monkeypatch) -> Contact:
    monkeypatch.chdir(tmp_path)
    return add_contact(Contact(company="Acme", name="Jo Recruiter"))


def _draft(contact: Contact) -> None:
    outreach_draft(
        contact_id=contact.id,
        company="",
        role="FDE",
        application_id=None,
        jd=None,
        output=None,
        max_tokens=900,
    )


def _drafted_message() -> Path:
    return next(
        p for p in Path("data/outreach-drafts").glob("*.md") if not p.name.endswith(".redteam.md")
    )


def test_outreach_draft_send_writes_review_beside_the_message(tmp_path, monkeypatch, capsys) -> None:
    contact = _make_contact(tmp_path, monkeypatch)
    monkeypatch.setattr("job_hunt.cli.outreach._run_one_shot_prompt", lambda **kw: "Hi Jo, here's why I'm reaching out.")
    monkeypatch.setattr(svc, "run_review", _fake_run_review("SEND", "VERDICT: SEND — looks fine"))

    _draft(contact)

    draft = _drafted_message()
    assert draft.read_text(encoding="utf-8") == "Hi Jo, here's why I'm reaching out."
    review = draft.with_name(f"{draft.stem}.redteam.md")
    assert review.read_text(encoding="utf-8") == "VERDICT: SEND — looks fine"
    assert "RED TEAM: SEND" in capsys.readouterr().out


def test_outreach_draft_block_is_loud_but_keeps_the_message(tmp_path, monkeypatch, capsys) -> None:
    contact = _make_contact(tmp_path, monkeypatch)
    monkeypatch.setattr("job_hunt.cli.outreach._run_one_shot_prompt", lambda **kw: "Hi Jo, here's why I'm reaching out.")
    monkeypatch.setattr(svc, "run_review", _fake_run_review("BLOCK", "VERDICT: BLOCK — wrong email"))

    _draft(contact)

    draft = _drafted_message()
    # BLOCK does not delete or withhold the drafted text — the operator adjudicates.
    assert draft.read_text(encoding="utf-8") == "Hi Jo, here's why I'm reaching out."
    review = draft.with_name(f"{draft.stem}.redteam.md")
    assert review.read_text(encoding="utf-8") == "VERDICT: BLOCK — wrong email"
    out = capsys.readouterr().out
    assert "RED TEAM: BLOCK" in out
    assert "Do not send" in out


def test_outreach_draft_unreviewed_is_not_a_pass_and_names_no_file(tmp_path, monkeypatch, capsys) -> None:
    contact = _make_contact(tmp_path, monkeypatch)
    monkeypatch.setattr("job_hunt.cli.outreach._run_one_shot_prompt", lambda **kw: "Hi Jo, here's why I'm reaching out.")
    monkeypatch.setattr(svc, "run_review", _fake_run_review("UNREVIEWED", "", ["red team unavailable: mmx not on PATH"]))

    _draft(contact)

    draft = _drafted_message()
    review = draft.with_name(f"{draft.stem}.redteam.md")
    assert not review.exists()
    out = capsys.readouterr().out
    assert "RED TEAM: UNREVIEWED" in out
    assert "not reviewed" in out
    # Never send the reader to a review file that was never written.
    assert ".redteam.md" not in out


# ----- apply-answers -----


def _answer(tmp_path, monkeypatch, *, jd: str | None = None) -> None:
    monkeypatch.chdir(tmp_path)
    apply_answers(
        company="Acme",
        role="FDE",
        form_text="Why do you want this role?",
        form_text_file=None,
        url=None,
        jd=jd,
        output=None,
    )


def test_apply_answers_send_writes_answers_and_review(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "job_hunt.nodes.apply_screen_assist.generate_apply_answers",
        _fake_generate_apply_answers("Because it combines applied AI and delivery."),
    )
    monkeypatch.setattr(svc, "run_review", _fake_run_review("SEND", "VERDICT: SEND — clean"))

    _answer(tmp_path, monkeypatch)

    # No tracker/report match for "Acme" / "FDE" here, so apply_answers falls
    # back to a company/role slug directory under output/.
    answers = Path("output/acme-fde-apply-answers.md")
    assert "Because it combines applied AI" in answers.read_text(encoding="utf-8")
    review = Path("output/acme-fde-apply-answers.redteam.md")
    assert review.read_text(encoding="utf-8") == "VERDICT: SEND — clean"
    assert "RED TEAM: SEND" in capsys.readouterr().out


def test_apply_answers_block_is_loud_but_keeps_the_answers(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "job_hunt.nodes.apply_screen_assist.generate_apply_answers",
        _fake_generate_apply_answers("Because it combines applied AI and delivery."),
    )
    monkeypatch.setattr(svc, "run_review", _fake_run_review("BLOCK", "VERDICT: BLOCK — unsupported claim"))

    _answer(tmp_path, monkeypatch)

    answers = Path("output/acme-fde-apply-answers.md")
    assert "Because it combines applied AI" in answers.read_text(encoding="utf-8")
    review = Path("output/acme-fde-apply-answers.redteam.md")
    assert review.read_text(encoding="utf-8") == "VERDICT: BLOCK — unsupported claim"
    out = capsys.readouterr().out
    assert "RED TEAM: BLOCK" in out
    assert "Do not send" in out


def test_apply_answers_unreviewed_is_not_a_pass_and_names_no_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "job_hunt.nodes.apply_screen_assist.generate_apply_answers",
        _fake_generate_apply_answers("Because it combines applied AI and delivery."),
    )
    monkeypatch.setattr(
        svc, "run_review", _fake_run_review("UNREVIEWED", "", ["red team timed out after 600s"])
    )

    _answer(tmp_path, monkeypatch)

    answers = Path("output/acme-fde-apply-answers.md")
    assert answers.exists()  # the answers themselves are never withheld
    review = Path("output/acme-fde-apply-answers.redteam.md")
    assert not review.exists()
    out = capsys.readouterr().out
    assert "RED TEAM: UNREVIEWED" in out
    assert "not reviewed" in out
    assert ".redteam.md" not in out


def test_apply_answers_jd_text_reaches_the_review(tmp_path, monkeypatch, capsys) -> None:
    """The TARGETING pass (CLAUDE.md §1) needs the actual posting, not an empty
    string — `--jd` must carry through to `run_review`'s `jd_text` verbatim."""
    monkeypatch.setattr(
        "job_hunt.nodes.apply_screen_assist.generate_apply_answers",
        _fake_generate_apply_answers("Because it combines applied AI and delivery."),
    )
    calls: list[dict] = []
    monkeypatch.setattr(svc, "run_review", _recording_run_review("SEND", "VERDICT: SEND — clean", calls))
    jd_file = tmp_path / "acme-fde.md"
    jd_file.write_text("ACME FDE JOB DESCRIPTION BODY", encoding="utf-8")

    _answer(tmp_path, monkeypatch, jd=str(jd_file))

    assert len(calls) == 1
    assert calls[0]["jd_text"] == "ACME FDE JOB DESCRIPTION BODY"
    out = capsys.readouterr().out
    assert "No JD supplied" not in out


def test_apply_answers_without_jd_warns_but_still_reviews(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "job_hunt.nodes.apply_screen_assist.generate_apply_answers",
        _fake_generate_apply_answers("Because it combines applied AI and delivery."),
    )
    calls: list[dict] = []
    monkeypatch.setattr(svc, "run_review", _recording_run_review("SEND", "VERDICT: SEND — clean", calls))

    _answer(tmp_path, monkeypatch, jd=None)

    assert len(calls) == 1
    assert calls[0]["jd_text"] == ""
    out = capsys.readouterr().out
    assert "No JD supplied" in out
    assert "RED TEAM: SEND" in out


# ----- linkedin outreach -----


def _linkedin(tmp_path, monkeypatch, *, jd: str | None = None) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "job_hunt.cli.outreach._run_one_shot_prompt",
        lambda **kw: "Hi Jo, here's why I'm reaching out.",
    )
    linkedin_outreach(
        company="Acme",
        role="FDE",
        jd=jd,
        output=None,
        max_tokens=900,
        with_search=False,
    )


def test_linkedin_outreach_send_writes_review_beside_the_message(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(svc, "run_review", _fake_run_review("SEND", "VERDICT: SEND — looks fine"))

    _linkedin(tmp_path, monkeypatch)

    # No --output given, so linkedin_outreach falls back to a company/role
    # slug path under output/, same convention as apply_answers.
    message = Path("output/acme-fde-linkedin.md")
    assert message.read_text(encoding="utf-8") == "Hi Jo, here's why I'm reaching out."
    review = Path("output/acme-fde-linkedin.redteam.md")
    assert review.read_text(encoding="utf-8") == "VERDICT: SEND — looks fine"
    assert "RED TEAM: SEND" in capsys.readouterr().out


def test_linkedin_outreach_block_is_loud_but_keeps_the_message(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(svc, "run_review", _fake_run_review("BLOCK", "VERDICT: BLOCK — wrong company"))

    _linkedin(tmp_path, monkeypatch)

    message = Path("output/acme-fde-linkedin.md")
    # BLOCK does not delete or withhold the drafted text — the operator adjudicates.
    assert message.read_text(encoding="utf-8") == "Hi Jo, here's why I'm reaching out."
    review = Path("output/acme-fde-linkedin.redteam.md")
    assert review.read_text(encoding="utf-8") == "VERDICT: BLOCK — wrong company"
    out = capsys.readouterr().out
    assert "RED TEAM: BLOCK" in out
    assert "Do not send" in out


def test_linkedin_outreach_unreviewed_is_not_a_pass_and_names_no_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        svc, "run_review", _fake_run_review("UNREVIEWED", "", ["red team unavailable: mmx not on PATH"])
    )

    _linkedin(tmp_path, monkeypatch)

    message = Path("output/acme-fde-linkedin.md")
    assert message.exists()  # the message itself is never withheld
    review = Path("output/acme-fde-linkedin.redteam.md")
    assert not review.exists()
    out = capsys.readouterr().out
    assert "RED TEAM: UNREVIEWED" in out
    assert "not reviewed" in out
    # Never send the reader to a review file that was never written.
    assert ".redteam.md" not in out
