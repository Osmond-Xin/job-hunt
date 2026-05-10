from __future__ import annotations

from job_hunt.services.web_extract import (
    _ashby_board_api_url,
    _company_from_ats_url,
    _find_ashby_job,
    _greenhouse_api_url,
    _lever_api_url,
    _workday_company_from_url,
    _workday_location_from_text,
    clean_web_text,
    extract_html_body,
    extract_html_title,
)


def test_greenhouse_job_url_maps_to_api_url() -> None:
    assert (
        _greenhouse_api_url("https://job-boards.greenhouse.io/cohere/jobs/123456")
        == "https://boards-api.greenhouse.io/v1/boards/cohere/jobs/123456"
    )


def test_company_from_known_ats_url() -> None:
    assert _company_from_ats_url("https://boards.greenhouse.io/faire/jobs/123") == "Faire"
    assert _company_from_ats_url("https://jobs.ashbyhq.com/acme-ai/abc") == "Acme Ai"


def test_lever_job_url_maps_to_api_url() -> None:
    assert (
        _lever_api_url("https://jobs.lever.co/acme/abc-123")
        == "https://api.lever.co/v0/postings/acme/abc-123"
    )


def test_ashby_job_url_maps_to_board_api_and_matching_job() -> None:
    url = "https://jobs.ashbyhq.com/ema/95d8ec49-2651-4b4a-8d7f-e39ab61031a7"
    assert _ashby_board_api_url(url) == "https://api.ashbyhq.com/posting-api/job-board/ema"
    assert _find_ashby_job({"jobs": [{"id": "95d8ec49-2651-4b4a-8d7f-e39ab61031a7"}]}, url) == {
        "id": "95d8ec49-2651-4b4a-8d7f-e39ab61031a7"
    }
    assert _find_ashby_job({"jobs": [{"id": "95d8ec49-2651-4b4a-8d7f-e39ab61031a7"}]}, f"{url}/application") == {
        "id": "95d8ec49-2651-4b4a-8d7f-e39ab61031a7"
    }


def test_html_extraction_removes_navigation_and_scripts() -> None:
    html = """
    <html>
      <head><title>Senior AI Engineer &amp; Platform</title></head>
      <body>
        <nav>Ignore this menu</nav>
        <script>ignore()</script>
        <main>
          <h1>Senior AI Engineer</h1>
          <p>Responsibilities include building agentic workflows.</p>
          <p>Requirements include Python and distributed systems.</p>
        </main>
      </body>
    </html>
    """

    assert extract_html_title(html) == "Senior AI Engineer & Platform"
    body = extract_html_body(html)
    assert "Ignore this menu" not in body
    assert "ignore()" not in body
    assert "Senior AI Engineer" in body
    assert "Responsibilities include building agentic workflows." in body


def test_clean_web_text_preserves_readable_line_breaks() -> None:
    text = clean_web_text("<h1>Role</h1><p>Line one<br>Line two</p>")
    assert text == "Role\nLine one\nLine two"


def test_workday_metadata_helpers_extract_company_and_location() -> None:
    url = (
        "https://acme.wd1.myworkdayjobs.com/en-US/Acme/job/Toronto/"
        "Senior-Software-Engineer_R0001"
    )

    assert _workday_company_from_url(url) == "Acme"
    assert _workday_location_from_text("Role\nlocations\nToronto\ntime type\nFull time") == "Toronto"
