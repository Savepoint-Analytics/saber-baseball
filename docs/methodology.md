# Methodology

How saber-baseball turns raw sources into trustworthy, reproducible analysis. This is
the living spec behind the roadmap in `README.md` and the modeling plan in
`PROJECT_CONTEXT.md` §7.

## Reproducibility contract

Every reported number must trace **raw source → transform → output** and re-run from a
clean checkout (`PROJECT_CONTEXT.md` §5). Concretely:

- Raw pulls land immutably in the date-partitioned **bronze** layer (Parquet in MinIO
  or `./data/bronze`); they are never edited in place.
- Transforms are **dbt** models (bronze → silver → gold), tested and documented.
- Models are tracked in **MLflow** (params, metrics, calibration), so any figure in a
  report points back to a run id and the commit that produced it.
- Data entering silver is validated by **Pandera** schemas (`src/schemas/`).

## Data flow

```
sources → src/ingestion (rate-limited, cached) → bronze Parquet
        → MinIO s3://sabermetrics/baseball/ (Savepoint-Lab)
        → dbt (duckdb) bronze views → silver → gold marts, all in DuckDB
        → src/warehouse/load_postgres → silver + gold copied into Postgres (baseball.*)
        → src/features, src/models, src/simulation → MLflow + serving
```

The ingest → dbt → publish leg of that chain is sequenced by **Prefect**
(`orchestration/flows.py`, daily at 09:00 ET). dbt tests run inside the flow, so a
failed test stops the run before anything reaches Postgres.

**Why transforms run in DuckDB and not against Postgres directly.** The bronze
models are `read_parquet()` views, and `read_parquet` is a DuckDB function that
Postgres does not have, so `dbt build --target postgres` fails to compile the bronze
layer and skips everything downstream. DuckDB therefore owns every transform and
test; `src/warehouse/load_postgres.py` then copies *only the modeled silver and gold
results* into Postgres for serving, via the DuckDB `postgres` extension. That is the
"push only modeled results into Postgres" rule from `CLAUDE.md` §4 — raw pitch-level
data never enters the warehouse, which is a licensing property as much as a
performance one.

**Why the marts land in a `baseball` schema.** Savepoint-Lab runs one Postgres
database (`sports`) shared by every vertical, so each vertical owns a schema inside
it — tennis publishes `tennis.*`, baseball publishes `baseball.*` (set via
`POSTGRES_SCHEMA`). Publishing to bare `silver`/`gold` schemas would collide with the
next vertical that did the same. The medallion layer is not lost by flattening
because it already lives in the table-name prefix (`sv_`, `gd_`).

## Joining Statcast to the game spine — join on `game_pk`, never on date

Statcast dates a suspended or resumed game by its **original** date; `sv_games`
dates it by the date it reached a final score (deliberately — see the model's own
comment, it is what keeps time-ordered features from peeking ahead). The two
therefore disagree for exactly those games.

Concretely: game_pk 716404 (Mets-Marlins, 2023) sits at `game_date = 2023-10-02` in
the spine, while its 236 pitches sit under `2023-09-28` in Statcast bronze. A
date-based join drops the game; a `game_pk` join is correct. This is also why the
backfill logs one "no rows" date per era — the pitches are not missing, they are
filed under the other date.

Coverage as of 2026-08-31: 8,184,213 pitches over 27,752 games, 2015-2026, and
**zero** games in `sv_games` lack Statcast rows.

## Modeling sequence (PROJECT_CONTEXT §3)

1. **Game-outcome prediction (primary).** Baselines first — home-field prior, Elo,
   Pythagorean, log5 (`src/models/baselines.py`) — then logistic → gradient boosting
   (XGBoost/LightGBM) on team-strength + pitching matchup + park + rest features.
   Judged on **log-loss** and **calibration** (reliability curve) on held-out seasons.
2. **Player & team analysis.** Context-adjusted value via RE24 / WPA from Retrosheet
   events (`src/features/run_expectancy.py`) — both features and "how games are won"
   explanation.
3. **Season/playoff projection.** Monte-Carlo the remaining schedule → playoff → World
   Series odds (`src/simulation/game_engine.py`), calibrated vs. history.
4. **Counterfactual simulation.** Bayesian partial-pooled player rates (PyMC/bambi);
   perturb an input skill and re-simulate to surface improvement levers.

## Baseline results (measured 2026-08-31)

Walk-forward over 2017-2026 (refit on all prior seasons for each held-out season;
regular season only; the 2020 and 2021 folds are excluded from the mean because the
60-game season distorts both). Produced by `python -m src.models.backtest`.

| Model | Log-loss | Brier | Accuracy | ECE |
|-------|----------|-------|----------|-----|
| **Elo** | **0.6788** | 0.2429 | 0.5694 | 0.0146 |
| Pythagorean (prior season) | 0.6821 | 0.2445 | 0.5634 | 0.0171 |
| Log5 (prior-season W-L) | 0.6836 | 0.2452 | 0.5616 | 0.0191 |
| Home-field constant | 0.6914 | 0.2491 | 0.5303 | 0.0079 |

**Elo at 0.6788 is the bar** the gradient-boosted and Bayesian models must beat.
For scale: 0.6931 is a coin flip, so every baseline sits close to it — single MLB
games are genuinely near-random, and honest gains here are small.

Two cautions when reading that table. Home-field has the *best* ECE purely because a
model that always predicts the base rate is trivially calibrated; calibration is a
necessary check, never a ranking metric on its own. And Pythagorean beats Log5
because run differential is the less luck-contaminated measure of the same strength
(year-over-year r = 0.62 vs 0.57 on 2015-2026).

## Statcast form does not beat Elo (measured 2026-09-01)

The first model over the pitch-level data. Features are 30-game rolling team form
(`gd_team_form`) — xwOBA created and allowed, wOBA, run-expectancy added, hard-hit
rate — differenced between the two teams and evaluated on the same walk-forward
harness.

| Model | Log-loss | Brier | Accuracy | ECE |
|-------|----------|-------|----------|-----|
| Elo + Statcast (logistic) | 0.6779 | 0.2425 | 0.5700 | 0.0160 |
| **Elo** | **0.6781** | 0.2426 | 0.5712 | 0.0150 |
| Statcast GBM (incl. Elo) | 0.6800 | 0.2435 | 0.5673 | 0.0179 |
| Statcast GBM (form only) | 0.6832 | 0.2451 | 0.5584 | 0.0156 |

**The honest reading: Statcast team form adds nothing measurable.** The logistic
blend's 0.0002 edge over Elo is not distinguishable from noise — paired per-game
t = 0.49 over 16,608 games, better in only 4 of 7 seasons. Elo remains the model to
beat. Reporting the blend as an improvement would be reading a rounding error.

Two things are worth keeping from the negative result:

- **The GBM lost to Elo while containing Elo as a feature** (0.6800 vs 0.6781). That
  is variance, not absent signal: with games this close to coin flips, a booster with
  enough capacity to find a small edge has more than enough to fit noise that swamps
  it. The linear model in log-odds space could not chase that noise and landed level
  with Elo. When the marginal signal is this thin, model capacity is a liability.
- **Team-level 30-game aggregates are probably the wrong shape.** They smear away the
  single largest per-game factor — the starting pitcher — along with bullpen state,
  rest, and park. A 30-game rolling mean of team xwOBA is close to a slow-moving
  restatement of team strength, which is exactly what Elo already encodes. That
  suggests the next attempt should be pitcher-level, not team-level.

## Starting-pitcher features (measured 2026-09-01)

The follow-up the team-form failure implied: replace 30-game team aggregates with
each **starting pitcher's** own last ten starts, split by times through the order
(`sv_statcast_pitcher_game` → `gd_starter_form`).

| Model | Log-loss | Gain vs Elo | Seasons won | per-game t |
|-------|----------|-------------|-------------|------------|
| Elo + starter form | **0.6770** | +0.00112 | 6/7 | 1.60 |
| Elo + starter + team form | 0.6770 | +0.00108 | 5/7 | 1.42 |
| Elo + team form only | 0.6779 | +0.00019 | 4/7 | 0.49 |
| Elo | 0.6781 | — | — | — |

**Read this as promising, not proven.** Starter form is roughly six times the effect
of team form and beats Elo in six of seven held-out seasons, but a paired per-game
t of 1.60 over 16,608 games does not clear the conventional 1.96 bar. It is the first
feature set that has moved the needle at all; it is not yet a result to publish.

Two supporting observations:

- **Adding team form on top of starter form makes it slightly worse** (5/7 seasons vs
  6/7). Team aggregates are not merely weak, they are largely redundant with what Elo
  and the starter already carry, and spending parameters on them costs more than it
  returns.
- **The pipeline independently reproduces the times-through-order penalty**, which is
  a good check that the TTO computation is right rather than plausible-looking:
  starter xwOBA allowed rises .3098 → .3270 → .3483 from the first time through the
  order to the third (+.0385).

TTO is counted exactly — how many times *this batter* has already faced *this
pitcher* in this game — rather than as plate-appearances/9, which drifts whenever a
lineup turns over irregularly (pinch hitters, a mid-inning pitching change), i.e.
exactly the situations TTO analysis cares about.

A note on what is and is not known before a game: the starter's **identity** is
announced a day or more ahead, so using it is not look-ahead. His performance *in*
the game is excluded by the lagged window. A live deployment would read the probable
starter from the Stats API instead of the completed game; nothing downstream changes.

## Bullpen and game context make it worse (measured 2026-09-01)

Bullpen quality and fatigue (`gd_bullpen_form`), then park factor and weather
(`gd_park_factor` + weather hydrated onto the schedule), added on top of the starter
model.

| Model | Log-loss | vs Elo | Seasons won |
|-------|----------|--------|-------------|
| **Elo + starter form** | **0.67691** | +0.00114 | 6/7 |
| Elo + starter + team form | 0.67696 | +0.00111 | 5/7 |
| Elo + starter + bullpen | 0.67739 | +0.00067 | 5/7 |
| Elo + starter + bullpen + context | 0.67771 | +0.00034 | 5/7 |
| Elo | 0.67805 | — | — |
| Elo + park/weather only | 0.67822 | −0.00016 | 2/7 |

**Both additions are significant — in the wrong direction.** Measured against the
starter model, paired per game:

| Addition | Δ log-loss | Seasons won | t |
|----------|-----------|-------------|---|
| + bullpen | −0.00047 | 1/7 | **−2.00** |
| + bullpen + context | −0.00080 | 2/7 | **−2.79** |
| context, on top of bullpen | −0.00033 | 2/7 | **−2.05** |

Not a regularisation artifact: the ordering (starter-only < +bullpen < +bullpen+context)
holds at every strength tested, C ∈ {0.01, 0.1, 1, 10}.

Why they fail is not the same in the two cases:

- **Bullpen** is largely already inside Elo. A team with a poor bullpen loses games it
  led, and Elo sees exactly that — the bullpen's contribution reaches the rating
  through the results it produced. The extra columns mostly restate it while costing
  variance. The one genuinely new signal, `bp_recent_bf` (fatigue, a *state* rather
  than a skill), is not strong enough on its own to pay for the other four.
- **Park and weather are symmetric.** Both clubs play the same park in the same
  weather, so these shift the *run environment*, not who wins. The temperature effect
  on scoring is real and large in this data — 8.43 runs/game at 40 °F rising to 10.30
  at 90 °F — but a shared effect on total runs is close to orthogonal to a
  home-win probability. It could only help through an interaction with roster
  construction, and no such interaction is detectable at this sample size.

**The consistent lesson across every experiment in this phase: feature count is a
liability here.** MLB games sit near a coin flip, so any feature that does not carry
signal beyond Elo costs more in variance than it returns. The ranked results are, in
order, Elo + starter, Elo + starter + team, Elo + starter + bullpen, Elo + starter +
bullpen + context — monotonically worse with every group added. `gd_bullpen_form`,
`gd_park_factor` and the weather columns are kept because they are correct, tested,
and useful for run-environment work (park-adjusted player value in Phase 2), but they
are **not** part of the best outcome model.

## Simulation engine: PA model and transition matrix (validated 2026-09-02)

Phases 4-5 need a plate-appearance engine, not a better game-level classifier —
final-score *distributions*, playoff odds, and counterfactuals are impossible from a
single win probability. Two halves are built and validated; the game loop is not
written yet, deliberately.

**Data.** No Retrosheet needed for 2015+. Statcast carries the full game state
(`on_1b/2b/3b`, `outs_when_up`, score), all 24 base-out states appear, and the
outcome vocabulary collapses to nine classes.

**PA outcome model** (`src/simulation/pa_model.py`) uses the **odds-ratio method**:

    raw(o) = batter(o) * pitcher(o) * state(o) / league(o)^2

Player rates are shrunk toward league by empirical Bayes with a beta-binomial
method-of-moments regression constant. The constants are *estimated*, and they land
on the published sabermetric values without being told them — K ≈ 53 PA (published
~60-70), BB ≈ 126 (~110-120) — while correctly regressing doubles (k ≈ 2937) and
reach-on-error (k ≈ 4497) almost entirely to league, both being luck rather than
skill. PyMC was skipped: it does not currently import (an ArviZ break), and every
Phase 3 experiment found added model capacity *cost* accuracy on this data.

**Transition matrix** (`gd_base_transitions`) is the empirical
`P(next state, runs | state, outcome)`, so baserunning is measured rather than
hand-coded. It reproduces the known pattern exactly: a single with a runner on second
scores him 37.8% of the time with none out, 51.7% with one out, 79.7% with two —
the runner goes on contact.

### Validation, before any game loop

| Check | Result |
|-------|--------|
| Analytic RE24 vs measured RE24 | mean abs diff **0.024**, max 0.090 |
| Expected runs per team-game | **4.426 vs 4.484 actual (−1.28%)** |
| Bases empty / 0 out | 0.498 vs 0.508 measured |
| Bases loaded / 0 out | 2.317 vs 2.395 measured |

The analytic solve uses *only* the transition matrix (`RE = (I − Q)⁻¹ b`), while the
measured RE24 is computed independently in dbt — so agreement tests the matrix on its
own, and any later disagreement is the game loop's fault.

**The residual −1.3% is understood, not unexplained.** The chain assumes a
league-average pitcher at every plate appearance. Reality selects: the pitcher who
has loaded the bases is a worse one. Pitcher quality by state correlates **+0.79**
with run expectancy (0.3251 on-base allowed with the bases loaded and none out
versus 0.3133 with bases empty and two out). Batter quality, by contrast, is flat
across states (0.321-0.324) — the first hypothesis, lineup clustering, was tested and
**rejected**. The game loop simulates specific pitchers and substitutes them, so it
does not inherit this assumption.

### Two bugs this validation caught

Both produced plausible numbers and would have survived into published results.

- **Walk-off half-innings were never excluded.** `outs_after` coalesces a missing
  next PA to 3, so `max(outs_after) = 3` for every half-inning and the filter matched
  nothing. It surfaced as 1.25% of bases-loaded home runs appearing to end an
  inning — which is impossible, and is now a dbt test.
- **0.135 runs per game were unattributed.** Runs scoring mid-PA (wild pitch, passed
  ball, steal of home) were dropped because runs were read off the PA's final pitch.
  Measuring first pitch to last took game reconciliation from 2,126/2,430 to
  2,420/2,430 and cut the run-scoring gap from −2.9% to −1.3%.

### Lineups and substitution policy

The third engine component (`src/simulation/lineup.py`), answering what a game loop
asks between plate appearances: who bats, and who is pitching.

**Batting slot is derived by rule, not inferred.** A team's k-th plate appearance is
slot `((k-1) mod 9) + 1`, because the order is fixed and a substitute inherits the
slot of the man he replaces. Exact, and it avoids reconstructing substitutions from
roster data we do not hold. Slot PA counts fall monotonically 22,442 (leadoff) to
18,174 (ninth) across 2025, as the rules require.

**The substitution policy is measured, not written.** A hand-coded rule ("pull the
starter after 100 pitches") encodes a guess about managerial behaviour, and that is
exactly what moves between eras. `gd_pitcher_removal` holds the empirical hazard:

| Times through order | Batters faced | Removal hazard |
|---|---|---|
| 1st | ≤6 | 0.004 |
| 2nd | 13-18 | 0.017 |
| 3rd | 19-24 | 0.145 |
| 3rd | 25-30 | 0.393 |
| 4th | 25-30 | 0.504 |

The third-time-through hook is visible in the data, and it tightened across eras
(hazard 0.174 in 2015-2018, 0.231 in 2019-2022, 0.218 in 2023-2026) — which is why
the table is bucketed by era rather than pooled.

**Validation:** sampling the hazard plate appearance by plate appearance, exactly as
the game loop will, produces starter outings of **21.56 batters faced against an
actual 21.75 (−0.9%)**, with matching median (22 vs 23) and 90th percentile (27 vs
27).

Bullpen selection is weighted by real appearance counts and excludes arms already
used in the game — without that exclusion a simulated team brings its best reliever
back twice and every bullpen looks deeper than it is.

### The game loop

`src/simulation/game_engine.py` steps the validated components into complete games.
**Vectorised across simulations, not plate appearances** — every simulation of a
matchup advances one PA in lockstep with finished half-innings masked out. A per-PA
Python loop over a full backtest is ~1.3 billion iterations; this runs at **~7,100
simulated games per second**.

Rules handled explicitly because each is easy to get silently wrong: the home team
does not bat in the ninth or later when ahead, a bottom-half rally stops the instant
the home team leads, and extra innings start with a runner on second from 2020.

**Home-field advantage is a fourth odds-ratio term**, not a fudge factor. Home and
away batters measurably differ — home batters strike out 4% less often, walk 5% more
and homer 3% more — and the term is fitted from that split. Without it the engine is
perfectly symmetric and returns a 50.4% home win rate, which would make every playoff
odds calculation wrong.

**Validation** against 2025, using real lineups, real starters and real bullpens:

| Quantity | Simulated | Actual | |
|---|---|---|---|
| Home runs/game | 4.536 | 4.490 | +0.046 |
| Away runs/game | 4.512 | 4.405 | +0.107 |
| **Home win %** | **52.6%** | **52.8%** (2023-26 era) | −0.2pp |
| 1-run games | 29.8% | 29.4% | +0.4pp |
| Runs/team mean | 4.480 | 4.447 | +0.7% |

Note the home-win comparison is against the **era the model was fitted on** (52.8%);
2025 alone ran high at 54.3%.

Faithfulness to the matrix is checked separately: with league-average players and
exactly nine innings the away team scores 4.501 against an analytic 9 × RE(0) of
4.479 — **1.8 standard errors**, statistically indistinguishable. So the loop steps
the transition matrix it was validated against, and the earlier apparent excess was
extra innings, not a bug.

### Known limitation: the engine is under-dispersed

| | Simulated | Actual |
|---|---|---|
| Runs/team, sd | 2.99 | **3.25** |
| Shutouts | 11.1% | 13.6% |
| 11+ run games | 3.9% | 5.2% |

The mean is right and the spread is ~8% too narrow: too few blowouts, too few
shutouts. This is the signature of assuming plate appearances are independent. Real
games carry correlated, game-level effects the engine does not yet model — park
(`gd_park_factor` exists and is unused here; Coors 1.272 vs T-Mobile 0.866),
temperature (measured at 8.43 runs/game at 40 °F rising to 10.30 at 90 °F), and
day-to-day variation in a pitcher's true form.

**This matters for Phase 4.** Under-dispersed game scores make playoff odds
*over-confident* — the tails are where postseason probability lives. Wiring park and
weather into the engine as game-level multipliers is the first thing to do before
trusting simulated World Series odds, and both data sources are already built.

## Evaluation

- **Primary metric:** log-loss vs. an Elo/home-field baseline (must beat it).
- **Calibration:** reliability curves + Brier score on held-out seasons.
- **Projection:** playoff/WS probabilities back-tested against actual outcomes.
- Each shipped model gets a **model card** (`docs/model-card-template.md`).

## Key open questions (tracked from PROJECT_CONTEXT §8)

- How far back to model (Statcast features only exist 2015+; older seasons need
  Retrosheet/Lahman proxies)?
- Park-factor and weather adjustment source + freshness.
- Bullpen/lineup uncertainty for pre-game prediction.
