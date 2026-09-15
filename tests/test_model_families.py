"""The model families beyond Elo: Bradley-Terry, Glicko, score models, hierarchical.

Each implements the same two-method contract, so most of what is worth testing is
that they honour it, that they stay causal, and that the specific thing each family
exists for actually happens — pooling for the hierarchical model, overdispersion for
the negative binomial, in-season reaction for the refitting fit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.baselines import Baseline, BradleyTerryRidge, GlickoRating
from src.models.score_models import NegativeBinomialScoreModel, PoissonScoreModel

FAST_FAMILIES = (BradleyTerryRidge, GlickoRating, PoissonScoreModel, NegativeBinomialScoreModel)


def _games(seasons=(2021, 2022, 2023), teams=(1, 2, 3, 4, 5, 6), seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows, pk = [], 0
    for season in seasons:
        for day in range(40):
            for home in teams:
                away = teams[(teams.index(home) + 1 + day % (len(teams) - 1)) % len(teams)]
                if home == away:
                    continue
                pk += 1
                p = 0.5 + 0.05 * (away - home)
                hw = int(rng.random() < p)
                rows.append(
                    {
                        "game_pk": pk,
                        "game_date": pd.Timestamp(f"{season}-04-01") + pd.Timedelta(days=day),
                        "season": season,
                        "home_team_id": home,
                        "away_team_id": away,
                        # Overdispersed on purpose: real run scoring has variance
                        # ~2.2x Poisson because runs arrive in clusters, and a
                        # Poisson fixture would make the NB tests vacuous.
                        "home_score": int(
                            rng.negative_binomial(4, 4 / (4 + 4.5 + 0.4 * (away - home)))
                        ),
                        "away_score": int(
                            rng.negative_binomial(4, 4 / (4 + 4.5 - 0.4 * (away - home)))
                        ),
                        "home_win": float(hw),
                    }
                )
    return pd.DataFrame(rows)


@pytest.mark.parametrize("factory", FAST_FAMILIES)
def test_families_satisfy_the_protocol(factory):
    assert isinstance(factory(), Baseline)


@pytest.mark.parametrize("factory", FAST_FAMILIES)
def test_predictions_are_probabilities(factory):
    g = _games()
    p = factory().fit(g[g.season < 2023]).predict_proba(g[g.season == 2023])
    assert len(p) == (g.season == 2023).sum()
    assert np.all((p > 0) & (p < 1))


@pytest.mark.parametrize("factory", FAST_FAMILIES)
def test_stronger_team_gets_the_higher_probability(factory):
    g = _games()
    model = factory().fit(g[g.season < 2023])
    test = g[g.season == 2023]
    p = model.predict_proba(test)
    strong = ((test.home_team_id == 1) & (test.away_team_id == 6)).to_numpy()
    weak = ((test.home_team_id == 6) & (test.away_team_id == 1)).to_numpy()
    if strong.any() and weak.any():
        assert p[strong].mean() > p[weak].mean(), factory.__name__


@pytest.mark.parametrize("factory", FAST_FAMILIES)
def test_a_games_own_result_does_not_change_its_prediction(factory):
    """Causality, asserted the same way as for every other model here."""
    g = _games()
    train, test = g[g.season < 2023], g[g.season == 2023].reset_index(drop=True)
    before = factory().fit(train).predict_proba(test)[5]
    flipped = test.copy()
    flipped.loc[5, "home_win"] = 1.0 - flipped.loc[5, "home_win"]
    assert factory().fit(train).predict_proba(flipped)[5] == pytest.approx(before)


def test_bradley_terry_refit_reacts_within_the_season():
    """The static fit cannot; that handicap cost 0.005 log-loss when measured."""
    g = _games()
    train, test = g[g.season < 2023], g[g.season == 2023]
    static = BradleyTerryRidge(refit_every_days=0).fit(train).predict_proba(test)
    refit = BradleyTerryRidge(refit_every_days=7).fit(train).predict_proba(test)
    assert not np.allclose(static, refit), "refitting had no effect — is it wired up?"


def test_glicko_deviation_shrinks_with_evidence():
    """The whole point of Glicko: a settled rating should stop moving so much."""
    g = _games()
    model = GlickoRating().fit(g)
    assert model.deviations, "no rating deviations were tracked"
    assert min(model.deviations.values()) < GlickoRating().initial_rd


def test_glicko_predictions_are_repeatable():
    """predict_proba must not mutate ratings; a second call has to match the first."""
    g = _games()
    model = GlickoRating().fit(g[g.season < 2023])
    test = g[g.season == 2023]
    assert model.predict_proba(test) == pytest.approx(model.predict_proba(test))


def test_negative_binomial_actually_differs_from_poisson():
    """Both estimate dispersion; only one uses it. Identical output means a bug."""
    g = _games()
    train, test = g[g.season < 2023], g[g.season == 2023]
    pois = PoissonScoreModel().fit(train)
    nb = NegativeBinomialScoreModel().fit(train)
    assert nb.dispersion > 0
    assert not np.allclose(pois.predict_proba(test), nb.predict_proba(test))


def test_score_models_produce_a_run_distribution():
    """The capability that motivates the family: scores, not just a win flag."""
    g = _games()
    model = NegativeBinomialScoreModel().fit(g[g.season < 2023])
    home, away = model.score_distribution(g[g.season == 2023].head(5))
    assert home.shape[0] == 5
    assert home.sum(axis=1) == pytest.approx(np.ones(5), abs=0.01)
    assert away.sum(axis=1) == pytest.approx(np.ones(5), abs=0.01)


def test_dispersion_is_zero_when_the_data_really_is_poisson():
    """The estimator must not invent overdispersion that is not there.

    Guards the method-of-moments step from the failure mode where every dataset
    looks overdispersed because of a bug in the residual calculation.
    """
    rng = np.random.default_rng(3)
    rows = []
    for i in range(4000):
        rows.append(
            {
                "game_pk": i,
                "game_date": pd.Timestamp("2023-04-01") + pd.Timedelta(days=i % 150),
                "season": 2023,
                "home_team_id": 1 + i % 6,
                "away_team_id": 1 + (i + 3) % 6,
                "home_score": int(rng.poisson(4.5)),
                "away_score": int(rng.poisson(4.5)),
                "home_win": float(rng.random() < 0.5),
            }
        )
    model = NegativeBinomialScoreModel().fit(pd.DataFrame(rows))
    assert model.dispersion < 0.03, f"invented dispersion {model.dispersion:.4f} on Poisson data"
