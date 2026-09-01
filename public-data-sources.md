# public-data-sources.md — Baseball Data Catalog & Licensing

> The reusable "inventory of already-understood data" for the baseball vertical.
> Each entry records **what it provides, how to access it, its license/ToS, whether
> commercial use is allowed, and the required attribution string.**
> Agents must check this file before pulling from any source.
> **Last updated:** 2026-06-28

---

## 0. The core distinction (read first)

A permissive **code** license (MIT on `pybaseball`) does **not** grant rights to the
**data** that code retrieves. Each upstream website/dataset has its own terms. The two
must be evaluated separately. Savepoint's commercial posture is:

> **Sell derived analysis, models, and visualizations. Never resell or redistribute a
> source's raw proprietary data. Always attach required attributions.**

---

## 1. Source catalog

### Retrosheet — play-by-play event data
- **Provides:** Pitch/event-level historical games, enabling run expectancy (RE24),
  WPA, situational analysis.
- **Access:** Event files; via `pybaseball` (`retrosheet` helpers) or direct download.
- **License/ToS:** Free for any use **including commercial** (sell, give away, build
  products) — *with required attribution*.
- **Commercial:** ✅ Allowed.
- **Required notice (must appear prominently):**
  > "The information used here was obtained free of charge from and is copyrighted by
  > Retrosheet. Interested parties may contact Retrosheet at www.retrosheet.org."
- Source: <https://www.retrosheet.org/notice.txt>

### Lahman Baseball Database — season-level history
- **Provides:** Seasonal batting/pitching/fielding, teams, standings back to 1871.
- **Access:** Published by **SABR** at <https://sabr.org/lahman-database/>, which
  links Box-hosted SQL / Access / comma-delimited downloads. Version 2025 (released
  2026-01-02) covers 1871-2025 and adds Negro Leagues statistics licensed from
  Seamheads. `src/ingestion/lahman.py` reads the SABR page, follows the
  comma-delimited link, and indexes the shared folder by filename at run time,
  because the Box file ids change with every annual release.
  *Note (2026-08-31):* the Chadwick Bureau **baseballdatabank** GitHub repo this
  previously used **no longer exists** — the repo 404s, so every archive URL
  (`main`, `master`, API-resolved default branch) fails. The only surviving GitHub
  mirror (`xorq-labs/baseballdatabank`) stops at 2021. Also do NOT rely on
  `pybaseball.lahman` — it hardcodes the same dead `master.zip` URL (issue #391).
- **Known gap:** `Salaries` ends at **2016** upstream; it is not maintained. Any
  payroll/salary-efficiency work needs another source.
- **License:** **Creative Commons Attribution-ShareAlike 3.0 (CC BY-SA 3.0).**
- **Commercial:** ✅ Allowed with attribution **and ShareAlike**.
- **ShareAlike caveat:** if we distribute a *derived database* built on Lahman, that
  derived database must carry CC BY-SA 3.0. This does **not** force our analysis code
  or reports to be CC-licensed — only a redistributed derivative *of the dataset*.
  Practically: don't redistribute the Lahman tables; distribute analysis instead.
- **Required attribution:** "Data from the Lahman Baseball Database, © 1996–2024
  Sean Lahman, licensed under CC BY-SA 3.0."
- Source: <http://seanlahman.com/download-baseball-database/>

### MLB Stats API / Statcast (Baseball Savant) — official + tracking data
- **Provides:** Schedules, live game state, boxscores, rosters (Stats API); pitch-level
  Statcast metrics — launch speed/angle, xwOBA, spin (Baseball Savant).
- **Access:** `MLB-StatsAPI` (toddrob) or direct `statsapi.mlb.com`; Statcast via
  `pybaseball.statcast*` (which reads baseballsavant.mlb.com).
- **License/ToS:** **MLB Advanced Media (MLBAM) copyright.** Governed by the MLB
  copyright notice (`http://gdx.mlb.com/components/copyright.txt`). Data is provided
  for personal, non-commercial use; redistribution/commercial exploitation requires
  MLBAM consent.
- **Commercial:** ⚠️ **Restricted.** Internal analysis and *derived* outputs are the
  safe path; **do not** redistribute raw MLB/Statcast data or build a product that
  resells it. For a fully-licensed commercial feed, pursue a licensed provider (§2).
- **Attribution:** Credit MLB Advanced Media / Baseball Savant where data is shown.
- **Operational:** respect rate limits; cache locally (see `CLAUDE.md`).

### Baseball Reference (Sports Reference family)
- **Provides:** Comprehensive historical/seasonal stats, standings, schedules.
- **Access:** `pybaseball` (BBRef scrapers), `baseballr`.
- **License/ToS:** Bots/scrapers that impact the site are prohibited; **rate limit 20
  requests/min** (10/min for FBref & Stathead). You **may** share/repackage data from
  *individual* pages — including commercially — **if you credit SRL** — but you may
  **not** build tools on *bulk-scraped* data or train generative AI on it without
  permission.
- **Commercial:** ⚠️ Conditional. Light, attributed, page-level use OK; bulk scraping
  for a product is not. Prefer Retrosheet/Lahman/Statcast for systematic pulls.
- **Required attribution:** credit "Sports Reference LLC / Baseball-Reference.com."
- Sources: <https://www.sports-reference.com/data_use.html>,
  <https://www.sports-reference.com/bot-traffic.html>

### FanGraphs
- **Provides:** Advanced metrics & leaderboards (wOBA, wRC+, projections incl. ZiPS,
  pitch values).
- **Access:** `pybaseball` / `baseballr` scrape FanGraphs pages.
- **License/ToS:** FanGraphs **does not support scraping, APIs, or automated export**
  and states so explicitly. No official public API.
- **Commercial:** ❌ **Do not scrape for commercial use.** Treat FanGraphs as
  reference for a human, or obtain data via a FanGraphs **membership** / direct
  permission. Do not bake scraped FanGraphs data into a sold deliverable.
- Source: <https://blogs.fangraphs.com/contact/>

### Chadwick Bureau Register — player ID crosswalk
- **Provides:** Mapping between MLBAM, Retrosheet, Baseball-Reference, FanGraphs IDs.
- **Access:** `pybaseball.playerid_lookup`; repo <https://github.com/chadwickbureau/register>.
- **License:** Open. **Commercial:** ✅ Allowed. Essential glue for joining sources.

### baseball.computer — pre-modeled historical DB
- **Provides:** Sabermetric historical tables (built on Retrosheet) queryable via DuckDB.
- **License:** Open / inherits Retrosheet attribution requirement.
- **Commercial:** ✅ with Retrosheet notice. Useful to bootstrap RE/WPA without
  reprocessing raw event files.

---

## 2. Fully-licensed commercial feeds (when the product scales)

If/when Savepoint sells data-derived products at scale and needs unambiguous
commercial rights, evaluate paid providers that license MLB data:

- **Sportradar** and **Stats Perform (Opta)** — official licensed sports data feeds.
- **Sports Info Solutions** — granular charting data (SR's own referral).
- These remove the ToS ambiguity of scraping and are the correct path for a paid
  product distributing underlying data. Budget-permitting only.

---

## 3. Commercial-use cheat sheet

| Source | Commercial OK? | Attribution required | Notes |
|--------|----------------|----------------------|-------|
| Retrosheet | ✅ Yes | ✅ Exact notice | Cleanest for commercial. |
| Lahman | ✅ Yes | ✅ + ShareAlike on redistributed data | Don't redistribute the dataset itself. |
| Chadwick register | ✅ Yes | Courtesy | ID crosswalk. |
| baseball.computer | ✅ Yes | ✅ (Retrosheet) | Built on Retrosheet. |
| MLB Stats API / Statcast | ⚠️ Restricted | ✅ MLBAM | Derived analysis only; no resale of raw data. |
| Baseball Reference | ⚠️ Conditional | ✅ SRL | Page-level + attributed OK; no bulk scrape. |
| FanGraphs | ❌ No (scraping) | — | Membership/permission only. |
| Licensed feeds (Sportradar etc.) | ✅ Per contract | Per contract | For scaled paid products. |

**Default safe stack for a sellable product:** Retrosheet + Lahman + Chadwick +
(MLB Statcast for *derived* metrics only) — all with their attributions attached.
