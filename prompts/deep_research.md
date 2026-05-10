{% include 'shared.md' %}

---

# Deep Research Prompt — {{ company }} ({{ role }})

Produce a structured research prompt the candidate can paste into a deep
research tool (Perplexity / Claude / ChatGPT with WebSearch). The prompt must
be self-contained and answerable by following its 6 numbered axes.

## Inputs

**Company**: {{ company }}
**Role**: {{ role }}
{% if jd_text %}
**JD excerpt**:
{{ jd_text[:1500] }}
{% endif %}
{% if cv_excerpt %}
**Candidate CV excerpt** (used in axis 6):
{{ cv_excerpt[:1500] }}
{% endif %}

## Task

Output a Markdown document with the literal title `## Deep Research:
{{ company }} — {{ role }}` followed by 6 sections. Personalize each section
with concrete signals from the JD above so the research stays specific.

### 1. AI / product strategy

- What AI/ML features ship in their products today?
- What is their AI stack (models, infra, tools)?
- Do they publish an engineering blog? Recent posts on AI?
- Conference talks / papers from their team?

### 2. Recent moves (last 6 months)

- Notable AI/ML/product hires?
- Acquisitions or partnerships?
- Product launches or pivots?
- Funding rounds or leadership changes?

### 3. Engineering culture

- Deploy cadence / CI-CD signals?
- Mono-repo or multi-repo?
- Languages / frameworks?
- Remote-first or office-first?
- Glassdoor / Blind sentiment about eng culture?

### 4. Likely challenges

- Scaling pain points (latency, cost, reliability)?
- Active migrations (infra, models, platforms)?
- Recurring complaints in reviews?

### 5. Competitors and differentiation

- Who are their main competitors?
- What is their moat / differentiator?
- How do they position vs the field?

### 6. Candidate's angle

Drawing on the CV above, the candidate's narrative, and the role
requirements:
- What unique value does the candidate bring to this team?
- Which of the candidate's projects are most relevant?
- What story should the candidate lead with in the interview?

## Hard constraints

- Output Markdown only — no preamble, no closing remarks.
- Each axis must be specific to {{ company }}; do not produce a generic
  template. If the JD does not mention something (e.g. tech stack), say
  "Search: [query]" with a concrete WebSearch query.
- Axis 6 must reference at least one concrete proof point from the CV
  excerpt above when present.
