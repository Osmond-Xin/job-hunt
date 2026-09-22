"""Channels feed the companies they surface back into the direct-ATS tier."""

from __future__ import annotations

from datetime import date

from job_hunt.services import discovered_companies as dc

PIPELINE = """# Pipeline

- [ ] https://jobs.ashbyhq.com/loopio/2217 | Loopio | Data Engineer | Toronto | source: getro | posted 2026-09-19
- [ ] https://boards.greenhouse.io/clutch/jobs/61 | Clutch Canada | Platform Engineer | Toronto | source: getro
- [ ] https://job-boards.greenhouse.io/clutch/jobs/62 | Clutch Canada | Analyst | Toronto | source: getro
- [x] https://jobs.lever.co/waabi/6bcf | Waabi | Software Engineer | Toronto | source: getro
- [ ] https://www.adzuna.ca/details/5890 | MSVU | Functional Analyst | Halifax | source: adzuna
- [ ] https://boards.greenhouse.io/embed/job_app?for=acme | Acme | Engineer | Toronto | source: websearch
- [ ] https://apply.workable.com/j/EA65B930B4 | Invision AI | ML Engineer | Toronto | source: getro
- [ ] https://jobs.lever.co/vendasta/abc | Vendasta | Developer | Saskatoon | source: getro
"""


def test_each_company_board_is_harvested_once_whatever_host_spelling_it_arrived_on():
    found = dc.harvest(PIPELINE, [], today=date(2026, 9, 21))
    assert [item["name"] for item in found] == ["Loopio", "Clutch Canada", "Waabi", "Vendasta"]
    clutch = found[1]
    assert clutch["careers_url"] == "https://job-boards.greenhouse.io/clutch"
    assert clutch["discovered_via"] == "getro" and clutch["first_seen"] == "2026-09-21"


def test_aggregators_embeds_and_short_links_are_not_companies():
    names = {item["name"] for item in dc.harvest(PIPELINE, [])}
    assert names.isdisjoint({"MSVU", "Acme", "Invision AI"})


def test_an_employer_switched_off_in_the_config_is_never_re_added():
    """enabled: false is how one-role-per-employer closes an employer."""
    closed = [{"name": "Vendasta", "careers_url": "https://jobs.lever.co/vendasta", "enabled": False}]
    assert "Vendasta" not in {item["name"] for item in dc.harvest(PIPELINE, closed)}


def test_recording_appends_only_what_is_new_and_survives_a_reload(tmp_path):
    path = tmp_path / "discovered-companies.yml"
    first = dc.harvest(PIPELINE, [])
    assert dc.record(first, path) == 4
    assert dc.record(first, path) == 0
    assert [item["name"] for item in dc.load(path)] == ["Loopio", "Clutch Canada", "Waabi", "Vendasta"]


def test_the_hand_written_list_wins_over_a_discovered_duplicate():
    tracked = [{"name": "Loopio Inc.", "careers_url": "https://jobs.ashbyhq.com/loopio"}]
    merged = dc.merge_into(tracked, dc.harvest(PIPELINE, []))
    assert [item["name"] for item in merged][:2] == ["Loopio Inc.", "Clutch Canada"]
    assert sum(1 for item in merged if "loopio" in item["careers_url"]) == 1
