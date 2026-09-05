# Level & Strategy Analysis

You are a career coach assessing positioning strategy for a job application.

## Candidate Profile
- **Years of experience**: {{ profile.years_experience }}
- **Target roles**: {{ profile.target_roles | join(", ") }}
{% if profile.level_acceptance %}
- **Levels the candidate accepts**: {{ profile.level_acceptance }}
{% endif %}
{% if profile.relocation_stance %}
- **Relocation**: {{ profile.relocation_stance }}
{% endif %}

## Job
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}
**Seniority signals in JD**: {{ jd_meta.seniority }}

## Task
Analyse the level and application strategy. Address:

### Seniority fit
Is the candidate a natural fit, reaching up, or reaching down? Cite specific signals from the JD
(years required, scope, IC vs. manager expectations).

A role below the candidate's tenure is **not** a fit problem — down-levelling is a deliberate
standing decision (see "Levels the candidate accepts" above). Describe the gap plainly and move
straight to how the application should be framed. Do not recommend against applying on
seniority grounds, and do not describe the candidate as "overqualified" — downstream scoring
reads this section, and that word has been producing false-negative SKIPs.

### Application angle
Given the fit, what is the strongest angle for this candidate?
(e.g. "position as a senior IC transitioning to tech lead", "lead with ML infra depth", etc.)

### Risks
What could cause a quick screen-out? How should the candidate address these proactively?

### Comp expectation
If salary range is stated in the JD, comment on alignment with candidate's minimum (if known).
Otherwise note that range is unstated.

Output format: plain Markdown, no extra preamble.
