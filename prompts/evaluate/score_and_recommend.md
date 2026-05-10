{% include 'shared.md' %}

---

# Score & Recommend

You are a senior recruiter making a go/no-go recommendation.

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
Score this application on 5 dimensions (each 0–5, with weight):

| Dimension | Weight | Scoring guide |
|---|---|---|
| Technical fit | 30% | How well does the CV match hard requirements? |
| Level fit | 20% | Is the candidate at the right seniority level? |
| Domain / industry fit | 15% | Relevant domain or adjacent experience? |
| Growth / trajectory | 15% | Is the career arc pointing toward this role? |
| Company / culture fit | 20% | Do values, stage, work style align? |

Output a JSON object with this exact schema — no prose outside the JSON:

```json
{
  "dimensions": [
    {"dimension": "Technical fit",      "score": 0.0, "weight": 0.30, "rationale": "..."},
    {"dimension": "Level fit",          "score": 0.0, "weight": 0.20, "rationale": "..."},
    {"dimension": "Domain fit",         "score": 0.0, "weight": 0.15, "rationale": "..."},
    {"dimension": "Growth trajectory",  "score": 0.0, "weight": 0.15, "rationale": "..."},
    {"dimension": "Company fit",        "score": 0.0, "weight": 0.20, "rationale": "..."}
  ],
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

Thresholds:
- weighted_total >= 4.0 → "apply", generate_pdf = true
- 3.5 <= weighted_total < 4.0 → "maybe", generate_pdf = true (CV polish, but flag risk)
- weighted_total < 3.5 → "skip", generate_pdf = false

**Ethical use**: per the shared rules, weighted_total < 4.0 means the recommendation
SHOULD lean toward `skip` unless the candidate has a specific reason to override.
Recruiter time has cost — quality over quantity.

cover_letter_body: 3–4 paragraphs, grounded in CV evidence, tailored to the company.
top_bullets: the 3 strongest CV bullets rewritten to match this JD's language.
