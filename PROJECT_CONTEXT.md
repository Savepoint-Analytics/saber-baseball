# PROJECT_CONTEXT.md — Sports / Baseball (Sabermetrics)

> **Vertical:** Sports → Baseball (MLB Sabermetrics)
> **Inherits:** `../../COMPANY_CONTEXT.md`
> **Last updated:** 2026-08-31

---

## 1. One-line summary

Build an empirical, reproducible MLB analytics platform that predicts game outcomes,
explains *how* games are won, projects which teams reach the playoffs and World
Series, and supports counterfactual simulation for player evaluation and improvement.

## 2. Motivation

Alex has deep, lived baseball knowledge (see `COMPANY_CONTEXT.md` §4). The aim is to
convert that intuition into *empirical* understanding — the ability to walk into a
game and read it through data — serving three roles:

- **Coaching:** identify what actually drives wins and where a team/player is leaking value.
- **Player analysis:** evaluate players against context-adjusted baselines.
- **Prediction & simulation:** forecast outcomes, then simulate counterfactuals ("if
  this hitter cut chase rate by 5%, what happens to his expected runs?").

This vertical is also Savepoint's **flagship portfolio piece** — the first proof that
the platform thesis works end to end.

## 3. Primary objective & sequencing

Priority order confirmed with Alex:

1. **Game-outcome prediction (PRIMARY).** Win probability and final-score
   distributions per game; calibrated, backtested against historical seasons.
2. **Player & team analysis (SECOND).** Context-adjusted player value, team strength,
   "how games are won" decomposition (run expectancy, win probability added).
3. **Season/playoff projection.** Monte-Carlo the remaining schedule → playoff odds →
   World Series odds.
4. **Counterfactual simulation.** Perturb player skill inputs and re-simulate to
   surface improvement levers.

Build the data foundation once; layer (1) → (4) on top of it.

## 4. Scope

**In scope (initial):**
- MLB regular season + postseason, modern era (Statcast era 2015+ for pitch-level;
  Retrosheet/Lahman for deep history).
- Game-level outcome models, team-strength ratings (Elo / Bradley-Terry baseline →
  gradient-boosted and Bayesian models), run-expectancy and win-probability tables,
  Monte-Carlo season simulation.

**Out of scope (for now):**
- Real-time in-game streaming / live betting automation.
- Minor leagues, college, international (revisit later).
- Selling or redistributing any raw proprietary data feed (see compliance rules).

## 5. Success criteria

- **Prediction:** game win-probability model beats a naive home-field / Elo baseline
  on log-loss and is well-calibrated (reliability curve) on held-out seasons.
- **Projection:** season simulation produces playoff/WS probabilities that are
  calibrated against historical outcomes.
- **Reproducibility:** any reported metric is traceable from raw source → transform →
  model output, and re-runnable from a clean checkout.
- **Compliance:** every data source used has a documented license/ToS status and
  required attribution (see `public-data-sources.md`).

## 6. Current phase

**Phase 1 — Ingestion (in progress, as of 2026-06-28).** Phase 0 (foundations /
scaffolding) is complete: context files, dependency & data-source registries, storage
architecture, and the project skeleton all exist in the `saber-baseball` repo. The
repo now ships working, compliant bronze-layer pullers for Statcast, Retrosheet,
Lahman, Chadwick, and the MLB Stats API (shared rate-limit + cache + backoff;
auto-stamped attributions; Pandera schema gates). Models/features/simulation remain
documented stubs. Next: land Pandera-validated silver tables in Postgres and wire dbt
+ Prefect end to end.

## 7. Modeling approach (intended)

- **Baselines first:** home-field prior, Elo, Pythagorean expectation, log5.
- **Run expectancy / win probability:** build RE24 and WPA tables from Retrosheet
  event data; these are both features and explanatory tools ("how games are won").
- **Outcome models:** logistic regression → gradient boosting (XGBoost/LightGBM) on
  team-strength + pitching matchup + park + rest features.
- **Bayesian player rates:** partial pooling (PyMC/bambi) for stable skill estimates
  — ideal for small-sample players and for counterfactual perturbation.
- **Simulation:** plate-appearance-level Markov / Monte-Carlo engine to roll games
  and seasons forward; supports counterfactuals by swapping player rate inputs.

## 8. Key open questions

- How far back to model? (Statcast features only exist 2015+; older seasons need
  proxy features from Retrosheet/Lahman.)
- Park-factor and weather adjustments — source and freshness.
- Bullpen/lineup uncertainty handling for pre-game prediction.

## 9. Companion files

- Operating rules for agents: `CLAUDE.md`
- Agent quick-start (tool-agnostic): `AGENTS.md`
- Code/tool inventory + licenses: `registry.md`
- Data catalog + ToS: `public-data-sources.md`
- Build/run + layout: `README.md`
