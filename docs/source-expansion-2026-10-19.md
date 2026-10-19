# Source expansion 2026-10-19 — remote-region and start-up lanes

He asked on 2026-10-19 to apply mainly to remote-region employers and start-ups. Triage now reserves
40% of the slate for each (`balance.py`). This round made both lanes better supplied and cleaner.
Nothing is committed. `config/portals.yml` is gitignored.

## Added

| Lane | What | Where | Measured 2026-10-19 |
|---|---|---|---|
| remote | **Agnico Eagle** — new `taleo_rest` reader (Taleo's REST job board) | `job_hunt/services/employer_boards.py` `_taleo_rest_reader`; `portals.yml` employer_boards | 66 rows, 59 remote-region (Nunavut FIFO mines, Kirkland Lake, Detour Lake, Rouyn-Noranda, Val-d'Or); 3 pass the title filter (Systems Administrator, Business Applications) |
| remote | **City of Vernon** (Workday) | `portals.yml` workday_boards | 11 rows, all remote-region; 1 title-pass (IS Business Analyst) |
| remote | **Northwestel** (UKG) | `portals.yml` employer_boards | 15 rows, 14 in the North; 0 technical that day |
| remote | **Northwestern Polytechnic**, Grande Prairie (hrSmart) | `portals.yml` employer_boards | 24 rows, all remote-region; 1 title-pass |
| remote | **Canfor** (SuccessFactors) | `portals.yml` employer_boards | 70 rows, 14 remote-region (mills); the only title-pass was a manager role |
| remote | `_SMALL_COMMUNITY_RE` += detour lake, dobie, upper beaver (Agnico names its mine sites, not towns) | `job_hunt/services/triage.py` | — |
| start-up | **Primary Venture Partners** Getro board (network 1124) | `portals.yml` getro_boards | 22 Canada postings, incl. Lyric "Forward Deployed Solution Strategist / Supply Chain Engineer (Canada)" |
| start-up | **Getro maturity label**: rows whose organization is `ipo` / `acquisition` / `private_equity` or `head_count` ≥ 5 (1,001+ staff) are written as `source: getro_mature`, so the start-up lane skips them | `job_hunt/services/getro_boards.py` `_mature` | reduced run (4 queries × 2 pages × 23 boards): 1,232 rows, 600 mature; of the 602 that pass the title filter, 283 were mature (Okta, Scotiabank, GitLab, KPMG, Google, Lacework…) |
| start-up | **Getro employer from the posting URL** when Getro files the posting under another company (acquiree or fund) | `getro_boards.py` `_employer` | Qubit. → Coveo, One Way Ventures → Helm Ai, Tile → Life360, QuoteMachine → Lightspeed Commerce, Wysdom.AI → Calabrio, Tech Talent Canada → Achievers. A rebrand that keeps its stem (Fellow.app/fellowai, GlossGenius/geniusai) keeps Getro's name |

Tests: `tests/test_getro_boards.py` (+14 cases), `tests/test_employer_boards.py` (+2). The full suite passes: 1809 tests.

Known residue:
- Rows already in `data/pipeline.md` keep their old `source: getro` label and company name. Only new scans write the fixed ones.
- QuoteMachine's organization says "seed", so its 6 Lightspeed rows still count as start-up.
- Getro Community (8870) carries VC funds' own records ("Moonfire" pointing at promptengineerjobs.io). These are not fixable from the data.

## Probed and dead — do not retry

- **Job Bank per community**: `locationstring` is dropped (confirmed in `jobbank.py`'s docstring). `jobbank_direct` already reads every NOC in all 13 provinces and territories, so remote towns are covered there already.
- **Teck** `jobs.teck.com`: SuccessFactors "Unify" theme, which loads results by JS; the `successfactors` reader gets nothing.
- **No adapter** for these platforms (each employer would need new code):
  - NTPC: iCIMS classic, `careers-ntpc.icims.com`
  - Brandon and Cape Breton University: Dayforce, `can242/can232.dayforcehcm.com`
  - Qulliq Energy: Prevue, `qeccareers.prevueaps.ca`
  - North Bay Regional Health Centre: Workland, `atlas.workland.com/careers/nbrhc`
  - NL Health Services: ServiceNow, `nlhs.service-now.com/nlhsjobs`
  - Newmont, ATCO, BC Hydro, Manitoba Hydro, Yukon Energy: Phenom
- **Career pages that render by JS or link no ATS**:
  - Colleges: Yukon University, Coast Mountain, Selkirk, Assiniboine, UCN, Northern Lights College, CNA
  - Universities: Laurentian, Algoma, MUN/Grenfell
  - Cities: Prince George, North Bay
  - Employers: Cameco, Glencore, West Fraser
- **Unreachable or 404 careers URLs**: Aurora College, Cambrian, Keyano, Portage, NIC, UNBC, TRU, Yellowknife, Hay River, Timmins, Sault Ste. Marie, Prince Albert, Grande Prairie, RMWB, Thompson, Fort St. John, Dawson Creek, Cranbrook, Kenora, CBRM, Corner Brook, Miramichi, Bathurst, Yukon Energy, Arctic Co-op, Canadian North (403), NTHSSA (504).
  - Arctic College posts through gov.nu.ca (`nu_gov` source); Aurora College through GNWT careers (`gnwt` source).
- **Getro / fund boards with no usable board**:
  - No board, or a Getro 404: Two Small Fish, Whitecap, Front Row, Highline Beta, ScaleUP, Mistral VP, Creative Ventures, DMZ, Spring, BDC, Garage, Round13, Staircase, Forum, OMERS, Boldstart, Susa, Amplify
  - Ironspring (3755) has only 1 Canada posting
  - `ripple.getro.com` is Ripple the crypto company, not Ripple Ventures
  - First Round and CRV run on Consider
  - a16z and Greylock run custom boards
  - The earlier lists in memory `startup-channels-2026-09` and `source-expansion-2026-09-27` still stand.

## Needs him

Nothing is blocking. One optional item: if he wants Dayforce employers (City of Brandon, an RCIP community; Cape Breton University), a `dayforce` reader is the next cheapest adapter. Both expose `jobs.dayforcehcm.com/<tenant>/<site>`.
