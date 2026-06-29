# Compliance — how the rules are enforced in code

`CLAUDE.md` §5, `AGENTS.md`, and `public-data-sources.md` define *what* the rules are.
This doc records *where they live in the codebase* so compliance is auditable and
automatic rather than a thing humans must remember.

## The core distinction

A permissive **code** license (MIT on pybaseball) does not grant rights to the **data**
it retrieves. We **sell derived analysis, never the raw feed**, and always attach the
required attributions.

## Enforcement map

| Rule | Where enforced |
|------|----------------|
| Required attribution strings (exact wording) | `src/compliance/attributions.py` |
| Attributions stamped onto every export | `attribution_block` / `stamp_text`; `_storage.write_parquet` drops `NOTICE.txt` beside each dataset; Streamlit footer in `src/serving/app.py` |
| Rate limits (BBRef ≤20/min, Savant, StatsAPI) | `src/ingestion/_throttle.py` (`RateLimiter`, per-source `LIMITERS`), configured in `.env` |
| Caching (don't re-hit sources) | `_throttle.cached_get` (on-disk TTL) + `enable_pybaseball_cache()` |
| Backoff / polite retries | `_throttle.with_backoff`, `tenacity` |
| Statcast/MLBAM "derived only, no resale" | documented in `statcast.py` / `statsapi.py`; `RESTRICTED_REDISTRIBUTION` set flags these sources |
| No FanGraphs scraping in sold work | no FanGraphs puller exists; treat as human reference only |
| GPLv3 (MLB-StatsAPI) runtime-only | used as a dependency, never bundled/modified into a closed redistributable |

## Source quick reference

| Source | Commercial? | Attribution | Hard constraint |
|--------|-------------|-------------|-----------------|
| Retrosheet | ✅ | Exact notice (verbatim) | Notice must be displayed |
| Lahman | ✅ | CC BY-SA 3.0 | Don't redistribute raw tables |
| Chadwick | ✅ | Courtesy | — |
| Statcast / MLB Stats API | ⚠️ | © MLBAM | Derived analysis only; no resale; rate-limit + cache |
| Baseball Reference | ⚠️ | © SRL | ≤20 req/min; no bulk-scrape products |
| FanGraphs | ❌ | — | No scraping for sold work; membership only |

## Agent guardrail

If a task would require breaking any rule above (e.g. "scrape all of FanGraphs into a
sellable table"), **refuse and propose a compliant alternative** (`CLAUDE.md` §5).
