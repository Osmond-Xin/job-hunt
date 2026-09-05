# ADR-015: Quota-free direct board adapters (discovery tiers 4-7)

**Status:** Accepted
**Date:** 2026-08-13

## Context

Discovery before this decision was tiers 1-3: per-company direct ATS fetch
(Greenhouse/Lever/Ashby), per-company WebSearch, and cross-employer
WebSearch channels (`site:jobbank.gc.ca`, `site:indeed.ca`, ...). All
WebSearch-backed discovery shares one scarce resource — Brave's free tier is
~2k queries/month — and it was being spent badly. Measured on a full
national scan: the `site:jobbank.gc.ca` channel alone returned 294 hits, of
which only 10 were real postings (241 were `/marketreport/` occupation
pages, the rest search-result or outlook pages) — 3.4% precision on the
single highest-value board for an immigration-led search (Job Bank is the
federal board; LMIA/PR-track employers are required to post there).

Several of the sources worth adding are not searchable at all in a useful
way: Job Bank resolves a typed keyword to a NOC code server-side and drops
the keyword itself from the URL, so a WebSearch snippet can name the wrong
occupation; provincial government career sites and Workday's own CxS JSON
search are not indexed by a general web search in any structured form.

## Decision

Add four adapters that talk to a board's own search directly — no
WebSearch provider, no query quota, one HTTP (or one Playwright-avoiding
`curl`) round trip per page:

- **Tier 4 — Job Bank** (`services/jobbank.py`): queried by NOC 2021 code ×
  province, the one filter combination verified to actually work against
  the live site.
- **Tier 5 — public-sector boards** (`services/gov_boards.py`): GNWT, the
  Nova Scotia public service, Nova Scotia Health, WRHA, and New Brunswick.
  Every one of these is immigration-relevant in a way a generic aggregator
  is not — GNWT is NTNP-eligible, Nova Scotia and New Brunswick sit in the
  AIP region, Manitoba is MPNP — which is the actual reason this tier
  exists rather than being left to tier 3's WebSearch channels.
- **Tier 5b — regional tech-industry boards** (`services/regional_boards.py`):
  Digital Nova Scotia and Tech Manitoba. Association boards in small
  provinces carry small local employers that national aggregators rarely
  syndicate — Digital Nova Scotia alone surfaced a cluster of Halifax AI
  roles that months of keyword sweeps never returned.
- **Tier 6 — Workday CxS** (`services/workday_boards.py`): the JSON API
  behind every `*.myworkdayjobs.com` board, called directly once a tenant's
  URL has been seen once.
- **Tier 7 — Adzuna** (`services/adzuna.py`): a real aggregator REST API
  rather than a search-engine crawl. One call returns up to 50 structured
  rows; the WebSearch channel it replaces spent 78 queries to put 6 rows in
  the pipeline.

Every adapter in tiers 4-7 returns structured `employer` / `location` /
(where the source has one) `salary` and `posted`/`closes` fields, so they
are strictly better per result than the WebSearch channels covering the
same boards, not just cheaper. Rows from these tiers skip the ordinary
positive-title filter (`_accept_jobs(..., require_positive=False)` in
`services/scan.py`) because the source has already established the
occupation — by NOC code, by a curated employer list, or by category facet
— and requiring a second positive match on top of that was measured to
discard about half of Job Bank's results to title-naming variance alone.

Each adapter accepts a `stats` dict and reports `collected` / `errors` /
`truncated` per board. This was added deliberately, not as an
afterthought: the first version of tiers 4/5 computed this information and
no caller read it, so Digital Nova Scotia reading only its first page
(30 of 120 postings, silently dropping the one Halifax forward-deployed
role in the set) looked identical to a genuinely quiet week. `_board_coverage_warnings`
turns a truncated or errored sweep into a scan warning the operator sees.

## Consequences

- Discovery no longer competes with itself for Brave quota on boards that
  have a better direct path — tiers 4-7 run on every scan where `--company`
  is not set, regardless of whether a WebSearch provider is even
  configured.
- Four new source-specific parsers (five tenants, counting the three
  SuccessFactors sites) is real surface area to maintain against upstream
  markup changes — this is the accepted cost of precision. Each adapter's
  module docstring records which alternative boards were investigated and
  could not be added (Saskatchewan's Oracle CDN 404s the REST path, PEI
  runs Radware bot protection, Yukon 403s any non-browser agent,
  Newfoundland has no stable listing URL, CollabHub/CareerBeacon/techNL
  need a real browser session), so the next person does not re-attempt them
  blind.
- Adzuna is configured differently from the other three: it lives in
  `config/settings.yml::adzuna` with credentials from the environment
  (`ADZUNA_APP_ID` / `ADZUNA_APP_KEY`), following the same `*_env`
  convention as `web_search.api_key_env`, while Job Bank / gov boards /
  regional boards / Workday are all plain dict sections in
  `config/portals.yml`. This asymmetry is real, not a bug — Adzuna is the
  only tier-4-7 source that needs a secret pair rather than just a query
  shape — but it means "where is this source configured" has two right
  answers depending on which source you mean. See `docs/design.md`
  §Discovery.
- `config/portals.example.yml` and `config/settings.example.yml`, the
  files `job-hunt init` copies, do not carry tiers 4-7 sections. The
  authoritative shape of these boards' config exists only on the machine
  where `portals.yml` was hand-built. Recorded in `docs/design.md` as a gap
  the next config-file change should close, not fixed here.
