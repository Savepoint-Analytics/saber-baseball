# R side (`baseballr`) — use only where it beats pybaseball

Per `CLAUDE.md` §3, this project is **Python-first**. R is here only for the few tasks
where `baseballr` (MIT) has a function pybaseball lacks (e.g. certain wOBA/FIP/standings
helpers). Keep R surface area minimal and well-isolated.

## Setup (renv for reproducibility)

```r
install.packages("renv")
renv::init()                 # first time
renv::install("baseballr")
renv::snapshot()             # commit renv.lock
```

Restore on a new machine:

```bash
Rscript -e 'renv::restore()'
```

## Convention

- One script per task in this folder; write outputs to the same bronze layout as the
  Python pullers (date-partitioned Parquet) so downstream dbt/DuckDB treat them
  identically.
- Record any new R dependency in `../registry.md` with its license before use.
- Attribution rules are identical to the Python side — stamp source notices on exports.
