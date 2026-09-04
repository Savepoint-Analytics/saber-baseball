"""Baseline game-outcome models (Phase 3).

Per ``PROJECT_CONTEXT.md`` §7 the baselines come first and set the bar the
gradient-boosted and Bayesian models must beat on log-loss and calibration:

  * :class:`HomeFieldBaseline`   — constant P(home win).
  * :class:`Log5Baseline`        — matchup probability from prior-season win rates.
  * :class:`PythagoreanBaseline` — same, but strength from prior-season run
    differential, which predicts next season better than W-L does.
  * :class:`EloBaseline`         — sequential team-strength ratings.

Every model implements the same two-method contract (:class:`Baseline`) so they slot
into the harness in ``src.models.backtest`` unchanged.

**Causality.** Each model may only use information available before the game it is
predicting. Log5/Pythagorean read the *prior* season; Elo walks games in date order
and updates only after predicting. That is what makes the backtest honest rather
than a measure of hindsight.

The module-level functions (:func:`pythagorean_expectation`, :func:`log5`,
:func:`elo_update`) are the underlying primitives, kept public because they are
useful on their own and are what the classes are assembled from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

# Columns every model expects, matching the `sv_games` silver table.
REQUIRED_COLUMNS = (
    "season",
    "game_date",
    "home_team_id",
    "away_team_id",
    "home_score",
    "away_score",
    "home_win",
)

_NEUTRAL = 0.5
_EPS = 1e-9


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def pythagorean_expectation(runs_for: float, runs_against: float, exp: float = 1.83) -> float:
    """Pythagorean win expectation. The 1.83 exponent is the common MLB fit."""
    rf, ra = float(runs_for), float(runs_against)
    denom = rf**exp + ra**exp
    return _NEUTRAL if denom == 0 else rf**exp / denom


def log5(p_a: float, p_b: float) -> float:
    """Log5 probability that team A beats team B given their win rates (Bill James)."""
    num = p_a - p_a * p_b
    den = p_a + p_b - 2 * p_a * p_b
    return _NEUTRAL if den == 0 else num / den


def elo_update(
    rating_a: float, rating_b: float, a_won: bool, k: float = 20.0, home_adv: float = 0.0
) -> tuple[float, float]:
    """Single Elo update. ``home_adv`` is added to team A's rating for expectation."""
    exp_a = elo_expectation(rating_a, rating_b, home_adv)
    delta = k * ((1.0 if a_won else 0.0) - exp_a)
    return rating_a + delta, rating_b - delta


def elo_expectation(rating_a: float, rating_b: float, home_adv: float = 0.0) -> float:
    """P(A beats B) on the standard 400-point Elo scale."""
    return 1.0 / (1.0 + 10 ** (-((rating_a + home_adv) - rating_b) / 400.0))


def home_field_prior(home_wins: int, total_games: int) -> float:
    """Empirical P(home win) with light Beta(1,1) smoothing."""
    return (home_wins + 1) / (total_games + 2) if total_games >= 0 else _NEUTRAL


def _logit(p: float | np.ndarray) -> float | np.ndarray:
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def _expit(x: float | np.ndarray) -> float | np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _check_columns(games: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in games.columns]
    if missing:
        raise ValueError(f"games frame is missing required columns: {missing}")


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
@runtime_checkable
class Baseline(Protocol):
    """Two-method contract shared by every baseline and, later, every ML model."""

    name: str

    def fit(self, games: pd.DataFrame) -> Baseline: ...

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        """P(home team wins), one entry per row, in the row order given."""
        ...


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
@dataclass
class HomeFieldBaseline:
    """Predict the training-set home-win rate for every game.

    The floor: any model that cannot beat this has learned nothing about baseball.
    """

    name: str = "home_field"
    p_home: float = _NEUTRAL

    def fit(self, games: pd.DataFrame) -> HomeFieldBaseline:
        _check_columns(games)
        y = games["home_win"].dropna()
        self.p_home = home_field_prior(int(y.sum()), int(len(y)))
        return self

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        return np.full(len(games), self.p_home, dtype=float)


@dataclass
class _PriorSeasonStrength:
    """Shared machinery for the two prior-season-strength models.

    Both build one strength number per (season, team) from the season *before* the
    game, regress it toward .500, combine the two teams with log5, then add a
    home-field bump in log-odds space.

    ``shrink`` is fitted, not assumed: team strength regresses to the mean between
    seasons (roster churn, aging, luck), and the amount is an empirical property of
    the era. Fitting it also keeps the model honest — a shrink near 0 would be the
    model telling us prior-season strength carries no signal.
    """

    name: str = "prior_season"
    shrink: float = 1.0
    home_logit: float = 0.0
    strength: dict[tuple[int, int], float] = field(default_factory=dict)

    def _season_strength(self, games: pd.DataFrame) -> dict[tuple[int, int], float]:
        raise NotImplementedError

    def fit(self, games: pd.DataFrame) -> _PriorSeasonStrength:
        _check_columns(games)
        played = games.dropna(subset=["home_win"])
        self.strength = self._season_strength(played)

        # Home-field advantage as a log-odds offset.
        self.home_logit = float(
            _logit(home_field_prior(int(played["home_win"].sum()), len(played)))
        )

        # Fit shrink by regressing next-season team win% on this-season strength.
        rows: list[tuple[float, float]] = []
        team_year = self._team_season_winpct(played)
        for (season, team), strength in self.strength.items():
            nxt = team_year.get((season + 1, team))
            if nxt is not None:
                rows.append((strength - _NEUTRAL, nxt - _NEUTRAL))
        if rows:
            x = np.array([r[0] for r in rows])
            y = np.array([r[1] for r in rows])
            denom = float(np.sum(x * x))
            self.shrink = float(np.sum(x * y) / denom) if denom > 0 else 1.0
        return self

    @staticmethod
    def _team_season_winpct(games: pd.DataFrame) -> dict[tuple[int, int], float]:
        long = pd.concat(
            [
                games[["season", "home_team_id", "home_win"]].rename(
                    columns={"home_team_id": "team_id", "home_win": "won"}
                ),
                games[["season", "away_team_id", "home_win"]]
                .assign(home_win=lambda d: 1 - d["home_win"])
                .rename(columns={"away_team_id": "team_id", "home_win": "won"}),
            ]
        )
        agg = long.groupby(["season", "team_id"])["won"].mean()
        return {(int(s), int(t)): float(v) for (s, t), v in agg.items()}

    def _lookup(self, season: int, team_id: int) -> float:
        """Prior-season strength, regressed toward .500.

        An unknown team — an expansion club, or any team in the first season of the
        data — falls back to exactly .500 rather than being dropped, so the model
        always returns a prediction for every game and the metrics stay comparable
        across models.
        """
        raw = self.strength.get((season - 1, team_id))
        if raw is None:
            return _NEUTRAL
        return _NEUTRAL + self.shrink * (raw - _NEUTRAL)

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        _check_columns(games)
        out = np.empty(len(games), dtype=float)
        for i, (season, home, away) in enumerate(
            zip(
                games["season"].to_numpy(),
                games["home_team_id"].to_numpy(),
                games["away_team_id"].to_numpy(),
                strict=True,
            )
        ):
            sh = self._lookup(int(season), int(home))
            sa = self._lookup(int(season), int(away))
            out[i] = float(_expit(_logit(log5(sh, sa)) + self.home_logit))
        return out


@dataclass
class Log5Baseline(_PriorSeasonStrength):
    """Prior-season win percentage, regressed, combined via log5."""

    name: str = "log5_winpct"

    def _season_strength(self, games: pd.DataFrame) -> dict[tuple[int, int], float]:
        return self._team_season_winpct(games)


@dataclass
class PythagoreanBaseline(_PriorSeasonStrength):
    """Prior-season Pythagorean expectation from run differential, regressed, log5'd.

    Preferred over raw W-L because run differential is the less luck-contaminated
    measure of the same underlying strength, and so survives into the next season
    better (measurably: r = 0.62 vs 0.57 on 2015-2026).
    """

    name: str = "pythagorean"
    exp: float = 1.83

    def _season_strength(self, games: pd.DataFrame) -> dict[tuple[int, int], float]:
        long = pd.concat(
            [
                games[["season", "home_team_id", "home_score", "away_score"]].rename(
                    columns={
                        "home_team_id": "team_id",
                        "home_score": "rf",
                        "away_score": "ra",
                    }
                ),
                games[["season", "away_team_id", "away_score", "home_score"]].rename(
                    columns={
                        "away_team_id": "team_id",
                        "away_score": "rf",
                        "home_score": "ra",
                    }
                ),
            ]
        )
        agg = long.groupby(["season", "team_id"])[["rf", "ra"]].sum()
        return {
            (int(s), int(t)): pythagorean_expectation(row.rf, row.ra, self.exp)
            for (s, t), row in agg.iterrows()
        }


@dataclass
class EloBaseline:
    """Sequential Elo ratings with home advantage and between-season regression.

    Unlike the prior-season models, Elo keeps updating *within* the season being
    predicted: :meth:`predict_proba` walks games in date order and records the
    prediction before applying the result, so no game informs its own forecast. That
    within-season adaptation is exactly why Elo is the baseline worth beating.

    Defaults follow the usual MLB settings — a low ``k`` because a single baseball
    game is close to a coin flip and should move a rating very little.
    """

    name: str = "elo"
    k: float = 4.0
    home_adv: float = 24.0
    revert: float = 0.25
    initial: float = 1500.0
    ratings: dict[int, float] = field(default_factory=dict)
    _last_season: int | None = None

    def _walk(
        self,
        games: pd.DataFrame,
        ratings: dict[int, float],
        last_season: int | None,
    ) -> tuple[np.ndarray, int | None]:
        """Walk games in date order, predicting before updating ``ratings`` in place.

        Operates on whatever ratings dict it is handed rather than on ``self``, which
        is what lets :meth:`predict_proba` stay pure — see the note there.
        """
        # game_pk breaks ties so the walk order is fully determined by the data, not
        # by the caller's row order. Date alone is not unique (a full slate shares a
        # date) and neither is date+home_team (doubleheaders), so without this the
        # same games in a different order produce different ratings — which would
        # quietly break the reproducibility contract in docs/methodology.md.
        sort_key = ["game_date"] + (["game_pk"] if "game_pk" in games.columns else [])
        ordered = games.sort_values(sort_key, kind="stable")
        preds = np.empty(len(ordered), dtype=float)
        for i, row in enumerate(ordered.itertuples(index=False)):
            season = int(row.season)
            if last_season is not None and season != last_season:
                for team in ratings:
                    ratings[team] += self.revert * (self.initial - ratings[team])
            last_season = season

            home_id, away_id = int(row.home_team_id), int(row.away_team_id)
            rh = ratings.setdefault(home_id, self.initial)
            ra = ratings.setdefault(away_id, self.initial)
            preds[i] = elo_expectation(rh, ra, self.home_adv)
            if pd.isna(row.home_win):
                continue
            ratings[home_id], ratings[away_id] = elo_update(
                rh, ra, bool(row.home_win), self.k, self.home_adv
            )
        # Restore the caller's row order.
        return pd.Series(preds, index=ordered.index).reindex(games.index).to_numpy(), last_season

    def fit(self, games: pd.DataFrame) -> EloBaseline:
        _check_columns(games)
        ratings: dict[int, float] = {}
        _, last = self._walk(games.dropna(subset=["home_win"]), ratings, None)
        self.ratings, self._last_season = ratings, last
        return self

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        """P(home win), updating ratings *within* the window but not on ``self``.

        Elo has to keep learning across the season it is predicting — that in-season
        adaptation is the whole reason it beats the static baselines. But it must do
        so on a scratch copy: a ``predict_proba`` that mutated the fitted ratings
        would return different numbers every time it was called on the same games,
        and any second call (a calibration pass, a re-scored fold) would silently be
        reading ratings that had already absorbed those results.
        """
        _check_columns(games)
        preds, _ = self._walk(games, dict(self.ratings), self._last_season)
        return preds


@dataclass
class BradleyTerryRidge:
    """Team strengths fitted jointly by penalised logistic regression.

    Elo is an *online approximation* to this. It walks through games one at a time
    nudging two ratings by a fixed learning rate ``k``, which is a stand-in for the
    regularisation and the recency weighting that a batch fit can do properly. Here
    every game is used at once, the penalty is explicit, and the home-field term is
    estimated rather than assumed.

    **Recency is weighted, not ignored.** A plain Bradley-Terry fit treats a game from
    three seasons ago like last night's, which would lose to Elo for the obvious
    reason. Games are weighted by ``half_life_days``, so the model answers "how good
    is this team *now*" rather than "how good has this team been".

    The design matrix is one column per team: +1 when home, -1 when away, so a
    coefficient is that club's strength in log-odds and a matchup is their difference.
    """

    name: str = "bradley_terry"
    half_life_days: float = 365.0
    C: float = 1.0
    # Days between refits while predicting. Elo updates after every game; a batch fit
    # that never refits holds one rating from opening day to game 162 and cannot react
    # to a June trade. Measured, that handicap alone cost 0.006 log-loss -- more than
    # any feature this project has added. 0 disables refitting.
    refit_every_days: int = 7
    ratings: dict[int, float] = field(default_factory=dict)
    home_advantage: float = 0.0
    _history: pd.DataFrame | None = field(default=None, repr=False)

    def fit(self, games: pd.DataFrame) -> BradleyTerryRidge:
        _check_columns(games)
        self._history = games.dropna(subset=["home_win"]).copy()
        self._fit_ratings(self._history)
        return self

    def _fit_ratings(self, played: pd.DataFrame) -> None:
        from sklearn.linear_model import LogisticRegression

        teams = sorted(
            set(played["home_team_id"].astype(int)) | set(played["away_team_id"].astype(int))
        )
        index = {t: i for i, t in enumerate(teams)}

        design = np.zeros((len(played), len(teams)))
        rows = np.arange(len(played))
        design[rows, [index[int(t)] for t in played["home_team_id"]]] = 1.0
        design[rows, [index[int(t)] for t in played["away_team_id"]]] = -1.0

        dates = pd.to_datetime(played["game_date"])
        age_days = (dates.max() - dates).dt.days.to_numpy(dtype=float)
        weights = np.exp(-np.log(2.0) * age_days / self.half_life_days)

        # fit_intercept carries home-field advantage: the design is antisymmetric in
        # the two teams, so anything left over is the edge of batting last.
        model = LogisticRegression(max_iter=5000, C=self.C, fit_intercept=True)
        model.fit(design, played["home_win"].to_numpy(dtype=int), sample_weight=weights)

        coefs = model.coef_[0]
        # Ratings are only identified up to a constant, so centre them.
        coefs = coefs - coefs.mean()
        self.ratings = {int(t): float(coefs[i]) for t, i in index.items()}
        self.home_advantage = float(model.intercept_[0])

    def _probabilities(self, games: pd.DataFrame) -> np.ndarray:
        home = np.array([self.ratings.get(int(t), 0.0) for t in games["home_team_id"]])
        away = np.array([self.ratings.get(int(t), 0.0) for t in games["away_team_id"]])
        return np.asarray(_expit(home - away + self.home_advantage), dtype=float)

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        """Predict, refitting periodically on everything already played.

        Walks the games in date order and refits every ``refit_every_days`` on the
        training history plus the test games that have already happened. Each game is
        predicted *before* being added, so this stays causal — the same discipline
        Elo's walk uses.
        """
        _check_columns(games)
        if not self.refit_every_days or self._history is None:
            return self._probabilities(games)

        ordered = games.sort_values(["game_date", "game_pk"], kind="stable")
        dates = pd.to_datetime(ordered["game_date"])
        out = np.empty(len(ordered), dtype=float)

        history = self._history
        last_refit: pd.Timestamp | None = None
        start = 0
        for end in range(1, len(ordered) + 1):
            boundary = end == len(ordered) or (
                last_refit is not None
                and (dates.iloc[end] - last_refit).days >= self.refit_every_days
            )
            if last_refit is None:
                last_refit = dates.iloc[0]
                boundary = False
            if not boundary:
                continue

            chunk = ordered.iloc[start:end]
            out[start:end] = self._probabilities(chunk)
            played = chunk.dropna(subset=["home_win"])
            if len(played):
                history = pd.concat([history, played], ignore_index=True)
                self._fit_ratings(history)
            last_refit = dates.iloc[end - 1]
            start = end

        return pd.Series(out, index=ordered.index).reindex(games.index).to_numpy()


@dataclass
class GlickoRating:
    """Elo with an explicit uncertainty on every rating (Glicko).

    Elo moves each rating by a fixed ``k`` regardless of how much is already known
    about the team. That is the wrong amount twice over: too slow for a club we have
    barely seen, too fast for one with a settled 100-game record. Glicko carries a
    *rating deviation* (RD) alongside each rating and scales the update by it, so
    uncertain ratings move quickly and confident ones resist.

    Two places that matters in baseball. Early April, when last season's rating is
    stale and this season's evidence is thin — RD has grown over the winter, so the
    model updates fast. And after a deadline trade, where the club is genuinely a
    different team but Elo would need weeks of results to notice.

    RD also widens with idle time (``rd_growth_per_day``), which is what encodes "we
    know less about them than we did in September".

    Implemented as sequential per-game updates rather than batched rating periods:
    baseball plays nearly every day, so a period *is* a game, and it keeps the walk
    causal in the same way Elo's does.
    """

    name: str = "glicko"
    initial_rating: float = 1500.0
    initial_rd: float = 200.0
    min_rd: float = 25.0  # tuned on 2018-19 only; see docs/methodology.md
    max_rd: float = 250.0
    rd_growth_per_day: float = 1.5
    home_adv: float = 24.0
    ratings: dict[int, float] = field(default_factory=dict)
    deviations: dict[int, float] = field(default_factory=dict)
    _last_seen: dict[int, pd.Timestamp] = field(default_factory=dict, repr=False)

    _Q = np.log(10.0) / 400.0

    def _g(self, rd: float) -> float:
        """Damping factor: an uncertain opponent makes a result less informative."""
        return 1.0 / np.sqrt(1.0 + 3.0 * self._Q**2 * rd**2 / np.pi**2)

    def _state(self, team: int, when: pd.Timestamp) -> tuple[float, float]:
        rating = self.ratings.setdefault(team, self.initial_rating)
        rd = self.deviations.setdefault(team, self.initial_rd)
        last = self._last_seen.get(team)
        if last is not None:
            idle = max((when - last).days, 0)
            rd = min(np.sqrt(rd**2 + (self.rd_growth_per_day**2) * idle), self.max_rd)
        return rating, float(rd)

    def _walk(self, games: pd.DataFrame, collect: bool) -> np.ndarray:
        sort_key = ["game_date"] + (["game_pk"] if "game_pk" in games.columns else [])
        ordered = games.sort_values(sort_key, kind="stable")
        dates = pd.to_datetime(ordered["game_date"])
        preds = np.empty(len(ordered), dtype=float)

        for i, row in enumerate(ordered.itertuples(index=False)):
            when = dates.iloc[i]
            home, away = int(row.home_team_id), int(row.away_team_id)
            r_h, rd_h = self._state(home, when)
            r_a, rd_a = self._state(away, when)

            # Each side's expectation is damped by the *opponent's* uncertainty.
            g_a, g_h = self._g(rd_a), self._g(rd_h)
            e_h = 1.0 / (1.0 + 10 ** (-g_a * (r_h + self.home_adv - r_a) / 400.0))
            e_a = 1.0 / (1.0 + 10 ** (-g_h * (r_a - r_h - self.home_adv) / 400.0))
            preds[i] = e_h

            if pd.isna(row.home_win):
                continue
            outcome = float(row.home_win)

            for team, rating, rd, g_opp, expected, score in (
                (home, r_h, rd_h, g_a, e_h, outcome),
                (away, r_a, rd_a, g_h, e_a, 1.0 - outcome),
            ):
                d_sq_inv = self._Q**2 * g_opp**2 * expected * (1.0 - expected)
                denom = 1.0 / rd**2 + d_sq_inv
                self.ratings[team] = rating + (self._Q / denom) * g_opp * (score - expected)
                self.deviations[team] = float(
                    np.clip(np.sqrt(1.0 / denom), self.min_rd, self.max_rd)
                )
                self._last_seen[team] = when

        if not collect:
            return np.empty(0)
        return pd.Series(preds, index=ordered.index).reindex(games.index).to_numpy()

    def fit(self, games: pd.DataFrame) -> GlickoRating:
        _check_columns(games)
        self.ratings, self.deviations, self._last_seen = {}, {}, {}
        self._walk(games.dropna(subset=["home_win"]), collect=False)
        return self

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        """Walk the window on a scratch copy so repeated calls are identical."""
        _check_columns(games)
        saved = (dict(self.ratings), dict(self.deviations), dict(self._last_seen))
        try:
            return self._walk(games, collect=True)
        finally:
            self.ratings, self.deviations, self._last_seen = saved
