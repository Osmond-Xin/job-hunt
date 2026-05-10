from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from job_hunt.models.job import CandidateProfile
from job_hunt.nodes.pdf import detect_paper_size


def _render(**overrides) -> str:
    env = Environment(loader=FileSystemLoader("templates"), autoescape=False)
    template = env.get_template("cv.html.j2")
    base = dict(
        profile=CandidateProfile(
            name="Example Candidate",
            email="candidate.com",
            website="https://candidate.example.com",
            linkedin="https://www.linkedin.com/in/example-candidate/",
            github="https://github.com/example-candidate",
        ),
        company="Acme",
        role="AI Engineer",
        cv_raw="",
        summary_angle="",
        top_bullets=[],
        keywords=[],
        cover_letter_body="",
        fonts_dir="/abs/templates/fonts",
        paper_size="letter",
    )
    base.update(overrides)
    return template.render(**base)


def test_cv_template_renders_contact_links() -> None:
    html = _render()
    assert 'href="mailto:candidate.com"' in html
    assert 'href="https://candidate.example.com"' in html
    assert 'href="https://www.linkedin.com/in/example-candidate/"' in html
    assert 'href="https://github.com/example-candidate"' in html


def test_cv_template_includes_self_hosted_fonts() -> None:
    html = _render()
    assert "Space Grotesk" in html
    assert "DM Sans" in html
    assert "file:///abs/templates/fonts/space-grotesk-latin.woff2" in html
    assert "file:///abs/templates/fonts/dm-sans-latin.woff2" in html


def test_cv_template_renders_competency_grid_when_keywords_supplied() -> None:
    html = _render(keywords=["LangGraph", "RAG", "Playwright", "Claude API"])
    assert "competencies-grid" in html
    assert "LangGraph" in html
    assert "competency-tag" in html
    assert html.count("competency-tag") >= 4


def test_cv_template_paper_size_overridable() -> None:
    letter_html = _render(paper_size="letter")
    a4_html = _render(paper_size="A4")
    assert "size: letter" in letter_html
    assert "size: A4" in a4_html


def test_detect_paper_size_us_canada_letter() -> None:
    class Stub:
        def __init__(self, location: str) -> None:
            self.location = location

    assert detect_paper_size(Stub("Toronto, ON, Canada")) == "letter"
    assert detect_paper_size(Stub("San Francisco, CA, USA")) == "letter"
    assert detect_paper_size(Stub("Madrid, Spain")) == "A4"
    assert detect_paper_size(Stub("London, United Kingdom")) == "A4"
    assert detect_paper_size(Stub("")) == "A4"
    assert detect_paper_size(None) == "letter"  # safe default when no JD meta
