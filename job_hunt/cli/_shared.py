from __future__ import annotations

import asyncio
from pathlib import Path

import typer
import yaml

from job_hunt.services.web_extract import extract_url_text

# Used by both cli.evaluation (evaluate/evaluate_batch's source-type resolution,
# _partition_already_evaluated's identity check) and cli.apply (full_loop_from_url,
# _infer_loop_target). Neither of those depends on the other, so these three pure
# helpers live here rather than in either file, to avoid a cli.apply <-> cli.evaluation
# import cycle.


def _resolve_source_type(target: str, source_type: str) -> str:
    if source_type != "auto":
        if source_type not in {"url", "jd_text", "local_file"}:
            raise typer.BadParameter("source_type must be auto, url, jd_text, or local_file")
        return source_type
    if target.startswith(("http://", "https://")):
        return "url"
    # P2-10: `local:jds/foo.md` is treated as a URL — web_extract intercepts the
    # `local:` scheme and reads the file directly.
    if target.startswith("local:"):
        return "url"
    if Path(target).exists() or (Path("jds") / target).exists():
        return "local_file"
    return "jd_text"


def _extract_loop_url_metadata(url: str) -> dict[str, str]:
    try:
        result = asyncio.run(extract_url_text(url, min_chars=50))
    except Exception:
        return {}
    return {
        "title": result.title.strip(),
        "company": result.company.strip(),
        "location": result.location.strip(),
        "ats": result.ats.strip(),
        "adapter": result.adapter,
        "text": result.text.strip(),
    }


def _apply_profile_values() -> dict[str, object]:
    values = {
        "name": "Example Candidate",
        "first_name": "Example",
        "last_name": "Candidate",
        "email": "candidate@example.com",
        "phone": "555-0100",
        "linkedin": "https://www.linkedin.com/in/example-candidate/",
        "github": "https://github.com/example-candidate",
        "portfolio": "https://candidate.example.com",
        "location": "City, Region, Country",
        "country": "",
        "address": "123 Example Street",
        "city": "",
        "province": "",
        "postal_code": "",
        "phone_device_type": "Mobile",
        "source": "Company Website",
        "full_time_start": "",
        "graduation_date": "",
        "gpa_4_scale": "",
        # Co-op eligibility fields (used by Workday co-op forms)
        "cowork_eligibility_category": "",
        "cowork_eligibility_description": "",
        # Path to an unofficial transcript PDF for Workday upload; leave empty to skip
        "transcript_pdf": "",
        # Academic distinction/award proof is not a transcript. Use it only for
        # fields asking for honors or academic-achievement proof.
        "academic_distinction_pdf": "",
        # Legal/terms consent is never assumed. Set explicitly in profile.yml or
        # create storage/private/workday-consent-terms after the user approves.
        "workday_consent_terms_and_conditions": False,
        # Auto-submit profile gate. CLI ``--auto-submit`` is only honoured when
        # ``apply.auto_submit_enabled: true`` is also set in profile.yml.
        "apply_auto_submit_enabled": False,
        # Default to producing a one-page cover letter PDF on every evaluate run.
        # CLI ``--cover-letter`` overrides per-run.
        "apply_cover_letter_default": False,
    }
    profile_path = Path("profile/profile.yml")
    if not profile_path.exists():
        return values
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return values
    candidate = raw.get("candidate") or raw
    location = raw.get("location") or {}
    cowork = raw.get("cowork") or {}
    workday = raw.get("workday") or {}
    apply_section = raw.get("apply") or {}
    values.update(
        {
            "name": candidate.get("full_name") or candidate.get("name") or values["name"],
            "email": candidate.get("email") or values["email"],
            "phone": candidate.get("phone") or values["phone"],
            "linkedin": candidate.get("linkedin") or values["linkedin"],
            "github": candidate.get("github") or values["github"],
            "portfolio": candidate.get("portfolio_url") or candidate.get("website") or values["portfolio"],
            "location": candidate.get("location") or values["location"],
            "country": location.get("country") or values["country"],
            "address": location.get("address") or values["address"],
            "city": location.get("city") or values["city"],
            "province": location.get("province") or values["province"],
            "postal_code": location.get("postal_code") or values["postal_code"],
            "cowork_eligibility_category": cowork.get("eligibility_category") or values["cowork_eligibility_category"],
            "cowork_eligibility_description": cowork.get("eligibility_description") or values["cowork_eligibility_description"],
            "gpa_4_scale": cowork.get("gpa_4_scale")
            or candidate.get("gpa_4_scale")
            or values["gpa_4_scale"],
            "graduation_date": cowork.get("graduation_date")
            or candidate.get("graduation_date")
            or values["graduation_date"],
            "full_time_start": apply_section.get("full_time_start")
            or candidate.get("full_time_start")
            or values["full_time_start"],
            "transcript_pdf": cowork.get("transcript_pdf") or candidate.get("transcript_pdf") or values["transcript_pdf"],
            "academic_distinction_pdf": cowork.get("academic_distinction_pdf")
            or candidate.get("academic_distinction_pdf")
            or values.get("academic_distinction_pdf", ""),
            "workday_consent_terms_and_conditions": bool(
                workday.get("consent_terms_and_conditions")
                or candidate.get("workday_consent_terms_and_conditions")
            ),
            "apply_auto_submit_enabled": bool(
                apply_section.get("auto_submit_enabled")
            ),
            "apply_cover_letter_default": bool(
                apply_section.get("cover_letter_default")
            ),
        }
    )
    name_parts = values["name"].split()
    if name_parts:
        values["first_name"] = candidate.get("first_name") or name_parts[0]
        values["last_name"] = candidate.get("last_name") or " ".join(name_parts[1:]) or values["last_name"]
    return values
