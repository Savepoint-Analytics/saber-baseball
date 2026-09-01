"""Outcome models over Statcast team form (Phase 3).

The first model that uses the pitch-level data for something. Features are the
*difference* between the two teams' rolling Statcast form as known before the game
(`gd_team_form`), optionally plus Elo's own probability as a single column.

Including Elo as a feature is deliberate. The question this model exists to answer is
"does pitch-level data add anything **beyond** team strength?", and the honest way to
ask it is to hand the model Elo and see whether the Statcast columns earn any further
improvement. A variant without it is exposed via ``include_elo=False`` so the two can
be compared directly.

**Implementation uses sklearn's HistGradientBoostingClassifier**, not LightGBM or
XGBoost, both of which are listed in the `modeling` extra but fail to import on macOS
without a separate `brew install libomp`. HistGradientBoosting is the same
histogram-based algorithm with no system dependency, and it consumes NaN natively —
which matters here because a team's first games genuinely have no prior form and
imputing a value would invent signal that did not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.models.baselines import EloBaseline, _check_columns

# Form columns differenced between home and away. games_in_window is handled
# separately: it is a confidence measure, not a skill measure, so differencing it
# would be meaningless.
_DIFF_COLUMNS = (
    "form_off_xwoba",
    "form_def_xwoba",
    "form_off_woba",
    "form_def_woba",
    "form_off_run_exp",
    "form_def_run_exp",
    "form_net_run_exp",
    "form_off_hard_hit_rate",
    "form_def_hard_hit_rate",
)


def _require_form_columns(games: pd.DataFrame) -> None:
    """Fail with the fix in the message, not a bare KeyError three frames down."""
    missing = [
        f"{side}_{c}"
        for c in _DIFF_COLUMNS
        for side in ("home", "away")
        if f"{side}_{c}" not in games.columns
    ]
    if missing:
        raise ValueError(
            "Statcast form columns absent — load games with "
            f"load_games(with_form=True). Missing e.g. {missing[:3]}"
        )


@dataclass
class StatcastGBM:
    """Gradient-boosted classifier on Statcast form differences (+ optional Elo)."""

    name: str = "statcast_gbm"
    include_elo: bool = True
    max_iter: int = 400
    learning_rate: float = 0.03
    max_depth: int | None = 3
    min_samples_leaf: int = 300
    l2_regularization: float = 1.0
    # Elo ratings all start at 1500 and take about a season to spread out, so the
    # earliest training season carries an `elo_p_home` that is far more compressed
    # (sd 0.038 in 2015) than the mature one the model actually predicts with
    # (sd 0.073). Training on it teaches the model to discount its single best
    # feature. Dropping the burn-in season fixes a genuine covariate shift.
    burn_in_seasons: int = 1
    early_stopping: bool = True
    random_state: int = 0
    model: HistGradientBoostingClassifier | None = None
    feature_names: list[str] = field(default_factory=list)
    _elo: EloBaseline | None = None

    # -- design matrix ------------------------------------------------------
    def _require_form(self, games: pd.DataFrame) -> None:
        missing = [
            f"{side}_{c}"
            for c in _DIFF_COLUMNS
            for side in ("home", "away")
            if f"{side}_{c}" not in games.columns
        ]
        if missing:
            raise ValueError(
                "StatcastGBM needs Statcast form columns — load games with "
                f"load_games(with_form=True). Missing e.g. {missing[:3]}"
            )

    def _design(self, games: pd.DataFrame, elo: np.ndarray | None) -> pd.DataFrame:
        cols: dict[str, np.ndarray] = {}
        for c in _DIFF_COLUMNS:
            cols[f"diff_{c}"] = games[f"home_{c}"].to_numpy(dtype=float) - games[
                f"away_{c}"
            ].to_numpy(dtype=float)
        # Raw levels for the headline metric: a matchup of two strong teams is not
        # the same game as a matchup of two weak ones, and a difference alone
        # cannot express that.
        cols["home_form_net_run_exp"] = games["home_form_net_run_exp"].to_numpy(dtype=float)
        cols["away_form_net_run_exp"] = games["away_form_net_run_exp"].to_numpy(dtype=float)
        for side in ("home", "away"):
            cols[f"{side}_games_in_window"] = games[f"{side}_games_in_window"].to_numpy(dtype=float)
        if self.include_elo:
            if elo is None:
                raise ValueError("include_elo=True but no Elo feature supplied")
            cols["elo_p_home"] = elo
        return pd.DataFrame(cols, index=games.index)

    # -- contract -----------------------------------------------------------
    def fit(self, games: pd.DataFrame) -> StatcastGBM:
        _check_columns(games)
        _require_form_columns(games)
        train = games.dropna(subset=["home_win"])
        if self.burn_in_seasons:
            seasons = sorted(train["season"].unique())
            keep = seasons[self.burn_in_seasons :]
            # Never drop everything: with a short training window the burn-in season
            # may be all there is, and a model fitted on nothing is worse than one
            # fitted on imperfect features.
            if keep:
                train = train[train["season"].isin(keep)]

        elo_train = None
        if self.include_elo:
            # A *fresh* Elo walked over the training games: each game is predicted
            # from ratings that contain only earlier games, so the feature is causal
            # inside the training set too. Fitting Elo first and then predicting on
            # the same games would leak every training outcome into its own feature.
            elo_train = EloBaseline().predict_proba(train)
            self._elo = EloBaseline().fit(train)

        design = self._design(train, elo_train)
        self.feature_names = list(design.columns)
        self.model = HistGradientBoostingClassifier(
            max_iter=self.max_iter,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            min_samples_leaf=self.min_samples_leaf,
            l2_regularization=self.l2_regularization,
            # Early stopping on a held-out slice of the *training* window. Games are
            # near-coin-flips, so an unregularised booster will happily fit noise;
            # the validation split is entirely inside the training seasons, so it
            # cannot see the test season.
            early_stopping=self.early_stopping,
            validation_fraction=0.15 if self.early_stopping else None,
            n_iter_no_change=20,
            random_state=self.random_state,
        ).fit(design, train["home_win"].to_numpy(dtype=int))
        return self

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("StatcastGBM.predict_proba called before fit")
        _require_form_columns(games)
        elo = self._elo.predict_proba(games) if (self.include_elo and self._elo) else None
        design = self._design(games, elo).reindex(columns=self.feature_names)
        return self.model.predict_proba(design)[:, 1]


@dataclass
class StatcastGBMNoElo(StatcastGBM):
    """Statcast form only — isolates what the pitch data contributes on its own."""

    name: str = "statcast_gbm_no_elo"
    include_elo: bool = False


@dataclass
class EloStatcastLogistic:
    """Elo blended with Statcast form through a regularised logistic regression.

    This exists because :class:`StatcastGBM` *lost* to plain Elo (0.6800 vs 0.6781)
    despite being handed Elo as a feature. The reason is variance, not absent signal:
    MLB games sit near a coin flip, the marginal information in team-level Statcast
    form is small, and a booster with enough capacity to find it also has enough
    capacity to fit noise that swamps it. A linear model in log-odds space cannot
    chase that noise, and it does beat Elo.

    Elo enters as a log-odds offset rather than a probability, so that a game with no
    Statcast information degenerates cleanly back to Elo itself.
    """

    name: str = "elo_statcast_logit"
    C: float = 1.0
    burn_in_seasons: int = 1
    model: Pipeline | None = None
    feature_names: list[str] = field(default_factory=list)
    _elo: EloBaseline | None = None

    def _design(self, games: pd.DataFrame, elo: np.ndarray) -> pd.DataFrame:
        p = np.clip(elo, 1e-9, 1 - 1e-9)
        cols: dict[str, np.ndarray] = {"elo_logit": np.log(p / (1 - p))}
        for c in _DIFF_COLUMNS:
            cols[f"diff_{c}"] = games[f"home_{c}"].to_numpy(dtype=float) - games[
                f"away_{c}"
            ].to_numpy(dtype=float)
        # A missing form difference means "we know nothing extra", and zero is
        # exactly that in a differenced design — it leaves the Elo offset untouched
        # rather than inventing a lean either way.
        return pd.DataFrame(cols, index=games.index).fillna(0.0)

    def fit(self, games: pd.DataFrame) -> EloStatcastLogistic:
        _check_columns(games)
        _require_form_columns(games)
        played = games.dropna(subset=["home_win"])
        seasons = sorted(played["season"].unique())
        keep = seasons[self.burn_in_seasons :] if self.burn_in_seasons else seasons
        train = played[played["season"].isin(keep)] if keep else played

        # Same causality rule as StatcastGBM: a fresh Elo walked over the training
        # games for the training feature, a fitted Elo for prediction.
        elo_train = EloBaseline().predict_proba(train)
        self._elo = EloBaseline().fit(played)
        design = self._design(train, elo_train)
        self.feature_names = list(design.columns)
        self.model = Pipeline(
            [("scale", StandardScaler()), ("lr", LogisticRegression(max_iter=2000, C=self.C))]
        ).fit(design, train["home_win"].to_numpy(dtype=int))
        return self

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        if self.model is None or self._elo is None:
            raise RuntimeError("EloStatcastLogistic.predict_proba called before fit")
        _require_form_columns(games)
        design = self._design(games, self._elo.predict_proba(games))
        return self.model.predict_proba(design.reindex(columns=self.feature_names))[:, 1]
