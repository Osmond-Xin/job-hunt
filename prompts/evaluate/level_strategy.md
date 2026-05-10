# Level & Strategy Analysis

You are a career coach assessing positioning strategy for a job application.

## Candidate Profile
- **Years of experience**: {{ profile.years_experience }}
- **Target roles**: {{ profile.target_roles | join(", ") }}

## Job
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}
**Seniority signals in JD**: {{ jd_meta.seniority }}

## Task
Analyse the level and application strategy. Address:

### Seniority fit
Is the candidate a natural fit, reaching up, or overqualified? Cite specific signals from the JD
(years required, scope, IC vs. manager expectations).

### Application angle
Given the fit, what is the strongest angle for this candidate?
(e.g. "position as a senior IC transitioning to tech lead", "lead with ML infra depth", etc.)

### Risks
What could cause a quick screen-out? How should the candidate address these proactively?

### Comp expectation
If salary range is stated in the JD, comment on alignment with candidate's minimum (if known).
Otherwise note that range is unstated.

Output format: plain Markdown, no extra preamble.
