{% include 'shared.md' %}

---

# Score & Recommend

You are a senior recruiter making a go/no-go recommendation.

**Active mode: `{{ mode | default('full') }}`** — see docs/design-notes.md §N.
Apply the weight table and thresholds for this mode only. Do not second-guess
the mode; the operator already decided which kind of role they are hunting.

## Evaluation inputs

### Role Summary
{{ evaluation_blocks.role_summary }}

### CV Match
{{ evaluation_blocks.cv_match }}

### Level Strategy
{{ evaluation_blocks.level_strategy }}

### Company Research
{{ evaluation_blocks.comp_research }}

### Personalization Plan
{{ evaluation_blocks.personalization }}

## Task
Score this application on 5 dimensions (each 0–5, with weight). Use the table
below for the active mode.

{% if mode == "student" %}
### Weight table — STUDENT mode (intern / co-op hunt)

| Dimension | Weight | Scoring guide for a co-op / internship |
|---|---|---|
| Technical fit | 20% | Reasonable overlap with the JD's expected stack — strict mastery is NOT required for a co-op. |
| Level fit | 10% | Is this a meaningful learning step? Penalise only when the role is clearly senior-IC or management. |
| Domain / industry fit | 20% | Relevant domain or adjacent experience that builds toward the operator's full-time direction. |
| Growth / trajectory | 25% | Will this co-op produce a portfolio artefact, story, or measurable signal for the next full-time hunt? |
| Company / culture fit | 25% | Stage, mentorship density, intern-program quality, return-offer norms, work style. |
{% else %}
### Weight table — FULL mode (full-time hunt)

| Dimension | Weight | Scoring guide |
|---|---|---|
| Technical fit | 30% | How well does the CV match hard requirements? |
| Level fit | 20% | Is the candidate at the right seniority level? |
| Domain / industry fit | 15% | Relevant domain or adjacent experience? |
| Growth / trajectory | 15% | Is the career arc pointing toward this role? |
| Company / culture fit | 20% | Do values, stage, work style align? |
{% endif %}

Output a JSON object with this exact schema — no prose outside the JSON:

```json
{
{% if mode == "student" %}
  "dimensions": [
    {"dimension": "Technical fit",      "score": 0.0, "weight": 0.20, "rationale": "..."},
    {"dimension": "Level fit",          "score": 0.0, "weight": 0.10, "rationale": "..."},
    {"dimension": "Domain fit",         "score": 0.0, "weight": 0.20, "rationale": "..."},
    {"dimension": "Growth trajectory",  "score": 0.0, "weight": 0.25, "rationale": "..."},
    {"dimension": "Company fit",        "score": 0.0, "weight": 0.25, "rationale": "..."}
  ],
{% else %}
  "dimensions": [
    {"dimension": "Technical fit",      "score": 0.0, "weight": 0.30, "rationale": "..."},
    {"dimension": "Level fit",          "score": 0.0, "weight": 0.20, "rationale": "..."},
    {"dimension": "Domain fit",         "score": 0.0, "weight": 0.15, "rationale": "..."},
    {"dimension": "Growth trajectory",  "score": 0.0, "weight": 0.15, "rationale": "..."},
    {"dimension": "Company fit",        "score": 0.0, "weight": 0.20, "rationale": "..."}
  ],
{% endif %}
  "weighted_total": 0.0,
  "recommendation": "apply|maybe|skip",
  "recommendation_rationale": "...",
  "generate_pdf": true,
  "strengths": ["...", "..."],
  "gaps": ["...", "..."],
  "pdf_content": {
    "summary_angle": "...",
    "top_bullets": ["...", "...", "..."],
    "keywords": ["...", "..."],
    "cover_letter_body": "..."
  }
}
```

{% if mode == "student" %}
Thresholds (student mode — co-ops have lower tail risk, lower bar is correct):
- weighted_total >= 3.5 → "apply", generate_pdf = true
- 3.0 <= weighted_total < 3.5 → "maybe", generate_pdf = true
- weighted_total < 3.0 → "skip", generate_pdf = false

**Framing for student mode**: the operator already has 20 years of engineering
execution. Do not penalise the candidate for being "overqualified" relative to
co-op expectations — that pattern leads to false-negative SKIP on exactly the
roles they can legally take. Score this as "would this co-op be a meaningful
applied-learning step that builds signal for a full-time conversion?".

cover_letter_body: 3–4 paragraphs framed as a candidate choosing this co-op for
applied learning + future-fit, NOT as a 20-year veteran. Grounded in CV evidence.
top_bullets: the 3 strongest CV bullets rewritten in language that recruiters
screening intern / co-op applications will recognise.
{% else %}
Thresholds (full mode):
- weighted_total >= 4.0 → "apply", generate_pdf = true
- 3.5 <= weighted_total < 4.0 → "maybe", generate_pdf = true (CV polish, but flag risk)
- weighted_total < 3.5 → "skip", generate_pdf = false

**Ethical use**: per the shared rules, weighted_total < 4.0 means the recommendation
SHOULD lean toward `skip` unless the candidate has a specific reason to override.
Recruiter time has cost — quality over quantity.

cover_letter_body: 3–4 paragraphs, grounded in CV evidence, tailored to the company.
top_bullets: the 3 strongest CV bullets rewritten to match this JD's language.
{% endif %}
