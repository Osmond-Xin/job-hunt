{% include 'shared.md' %}

# Portfolio Project Evaluation

You are deciding whether the candidate should spend scarce job-search time on a
portfolio project.

## Project Idea
{{ project_idea }}

{% if role_context %}
## Target Role / Company Context
{{ role_context }}
{% endif %}

{% if cv_excerpt %}
## Candidate CV Excerpt
{{ cv_excerpt }}
{% endif %}

## Scoring

Score each dimension from 1-5 and justify briefly:

| Dimension | Weight | 5 means | 1 means |
|-----------|--------|---------|---------|
| Target-role signal | 25% | Directly proves a skill employers buy | Not related |
| Uniqueness | 20% | Distinctive and memorable | Everyone has this |
| Demo-ability | 20% | Live demo or 2-minute walkthrough is obvious | Only code / no visual proof |
| Metrics potential | 15% | Clear latency, cost, accuracy, UX, or business metrics | No measurable outcome |
| Time to MVP | 10% | One week or less | Three months or more |
| STAR-story potential | 10% | Rich trade-offs and decisions | Just implementation |

## Output

Return plain Markdown with:

1. `## Verdict`: one of `BUILD`, `SKIP`, or `PIVOT TO <alternative>`.
2. `## Scorecard`: weighted table.
3. `## Why`: 3-5 bullets.
4. `## 80/20 Plan`: week 1 MVP, week 2 polish/interview pack.
5. `## Interview Pack`: one-pager, demo, postmortem, and exact metrics to collect.
6. `## Risks`: what could make this not worth doing.
