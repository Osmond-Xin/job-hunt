# Classify Archetype

You are a technical recruiter classifying a job posting into a role archetype.

## Job Description
**Company**: {{ jd_meta.company }}
**Title**: {{ jd_meta.title }}

<<<JD_TEXT_BEGIN>>>
{{ jd_text }}
<<<JD_TEXT_END>>>

## Task
Classify this role into one of the following archetypes:

| Archetype | Signals |
|---|---|
| ml-engineer | ML systems, model serving, training infra, MLOps, feature stores |
| data-scientist | Analysis, experimentation, statistical modelling, notebooks, insights |
| data-engineer | Pipelines, ETL/ELT, data warehouse, Spark, dbt, streaming |
| backend-engineer | APIs, services, distributed systems, databases, reliability |
| frontend-engineer | UI, React/Vue/Angular, accessibility, design systems |
| fullstack-engineer | Both frontend and backend, product-focused |
| platform-engineer | Infra, Kubernetes, CI/CD, SRE, cloud architecture |
| ai-product | LLM products, RAG, agents, prompt engineering, AI application development |
| research-scientist | Publications, novel methods, PhD-level research, experimentation |
| engineering-manager | People management, team building, roadmap, cross-functional leadership |
| staff-plus | Architecture, org-wide technical strategy, principal/staff/distinguished |
| other | Does not fit above archetypes clearly |

Output a JSON object with this exact schema — no prose outside the JSON:

```json
{
  "archetype": "...",
  "confidence": 0.0,
  "rationale": "...",
  "key_signals": ["...", "..."]
}
```

confidence: 0.0–1.0. Use < 0.6 when the role spans multiple archetypes or signals are weak.
key_signals: 3–5 specific phrases from the JD that drove the classification.
