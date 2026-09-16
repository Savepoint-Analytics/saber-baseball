# PROJECT_CONTEXT.md — Sports / Baseball (Sabermetrics)

> **Vertical:** Sports → Baseball (MLB Sabermetrics)
> **Inherits:** `../../COMPANY_CONTEXT.md`
> **Last updated:** 2026-09-15

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

**Phases 1, 3 and 4 complete; Phase 2 in progress (as of 2026-09-15).** The build
order departed from §3's priority order deliberately: the data foundation and the
prediction/simulation stack were finished first because player evaluation needed
the plate-appearance layer they produced.

- **Phase 1 — Ingestion: done.** Compliant bronze pullers for Statcast, Retrosheet,
  Lahman, Chadwick, the Stats API and the team dimension (shared throttle + cache +
  backoff; auto-stamped attributions; Pandera gates). 8.2M pitches over 2015-2026 in
  MinIO; dbt silver/gold in DuckDB (168 tests); curated marts published to the
  `baseball` schema in Postgres; Prefect chains ingest → dbt → publish daily.
- **Phase 3 — Prediction: baselines and nine model families backtested.** Elo
  (0.6781 log-loss walk-forward) is still the bar; Elo + starting-pitcher form
  (0.6769, wins 6 of 7 seasons) is the only feature set that has moved it, and it
  is not yet significant (t = 1.60). Bullpen, park/weather and batter × pitch-family
  features were measured and make prediction worse. `make today` publishes the slate.
- **Phase 4 — Season simulation: done, with a documented caveat.** The
  plate-appearance engine reproduces run scoring, home-field advantage and 1-run
  frequency but is ~6% under-dispersed after six measured mechanisms. Playoff odds
  are backtested from 1 September 2023-25: locks and also-rans are calibrated, the
  75-95% band is overconfident (p = 0.013 against a null that respects twelve
  qualifiers per league), and the report carries that note.
- **Phase 2 — Player & team analysis: started 2026-09-15.** `sv_players` (Chadwick ∪
  Statcast ids, Lahman bio), per-season RE24 credited to every batter and pitcher,
  season linear weights (they reproduce the textbook values), and
  `gd_batter_season` / `gd_pitcher_season` with park-adjusted runs above average;
  `src/features/player_value.py` joins the odds-ratio model's shrunk talent rates
  into a value card. Win probability and leverage are solved analytically from the
  engine's components (`gd_win_probability`; martingale-exact, 0.011 calibration
  gap against real games) and credited as WPA / WPA-per-LI / mean leverage. Not
  yet built: defence, baserunning, salary, any serving surface beyond the CLI.

- **Phase 5 — Counterfactuals: first cut, 2026-09-15.** `src/simulation/counterfactual.py`
  perturbs one player's rate vector and re-runs his real games paired on common
  random numbers (arms correlate +0.95; standard error ~5x smaller than independent
  runs). Reports an analytic linear-weights delta and a simulated one in runs and
  wins. Ohtani, three points of K → 1B: +0.081 runs/game, +1.18 ± 0.50 wins over
  his 158 games.

**Next:** package the counterfactual and value-card outputs as products (a serving
surface beyond the CLI); then defence, baserunning and salary.

## 7. Modeling approach (intended)

- **Baselines first:** home-field prior, Elo, Pythagorean expectation, log5.
- **Run expectancy / win probability:** RE24 is built per season from Statcast
  plate appearances (which carry the full base-out state for 2015+, so Retrosheet is
  not needed there); the win-probability table is solved analytically from the
  same transition matrix. Both features and explanatory tools ("how games are won").
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
