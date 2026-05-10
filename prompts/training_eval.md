{% include 'shared.md' %}

# Training / Certification Evaluation

You are deciding whether the candidate should spend scarce job-search time on a
course, certificate, workshop, or learning program.

## Training Option
{{ training_option }}

{% if role_context %}
## Target Role / Company Context
{{ role_context }}
{% endif %}

{% if cv_excerpt %}
## Candidate CV Excerpt
{{ cv_excerpt }}
{% endif %}

## Evaluation Dimensions

Assess:

| Dimension | Question |
|-----------|----------|
| North-star alignment | Does it move the candidate toward target roles? |
| Recruiter signal | What will hiring managers infer from seeing it? |
| Time and effort | Weeks and hours per week |
| Opportunity cost | What higher-signal work does it displace? |
| Risks | Outdated content, weak brand, too basic, too theoretical |
| Portfolio artifact | Does it produce a demonstrable project or proof point? |

Prioritize learning that improves credibility in production-grade AI:
LLM evals/testing, observability, cost/reliability trade-offs, governance/safety,
and enterprise AI architecture.

## Output

Return plain Markdown with:

1. `## Verdict`: one of `DO`, `DO WITH TIMEBOX`, or `DO NOT DO`.
2. `## Scorecard`: concise table across the dimensions.
3. `## Better Alternative`: if any.
4. `## Timebox Plan`: 4-12 week plan, or a shorter plan if timeboxed.
5. `## CV / Interview Use`: exactly how to mention the result, if at all.
6. `## Risks`: conditions that would change the recommendation.
