# Model card — <model name> (v<version>)

> Fill one of these out for every shipped model. Keep it next to the MLflow run id.

## Overview
- **Purpose:** (e.g. pre-game home-win probability)
- **Owner / date:**
- **MLflow run id / git commit:**
- **Status:** baseline | candidate | production

## Data
- **Sources used:** (Retrosheet / Lahman / Statcast / StatsAPI / Chadwick)
- **Seasons / window:**
- **Required attributions:** (auto from `src/compliance/attributions.py`)
- **Known data limitations:**

## Features
- **Inputs:** (team strength, pitching matchup, park, rest, …)
- **Leakage checks performed:**

## Training
- **Algorithm + library:**
- **Hyperparameters:**
- **Train/validation/test split (by season):**

## Evaluation
- **Primary metric (log-loss) vs. baseline:**
- **Calibration (reliability curve / Brier):**
- **Backtest seasons + results:**

## Intended use & limits
- **Appropriate uses:**
- **Out-of-scope uses:** (no live betting automation; not club-grade unless stated)
- **Uncertainty communicated as:**

## Ethical / commercial notes
- Derived analysis only — no raw-feed redistribution.
- Public-data limitations disclosed in any deliverable.
