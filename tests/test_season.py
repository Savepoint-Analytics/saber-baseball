"""Season projection to playoff odds (Phase 4).

The invariants here are the ones a bracket bug does not announce: exactly twelve
clubs qualify, exactly six win divisions, exactly one wins the World Series. A
seeding error produces a plausible-looking table that is quietly wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import settings
from src.simulation.season import (
    PLAYOFF_TEAMS_PER_LEAGUE,
    load_season_state,
    project_season,
)

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")


@pytest.fixture(scope="module")
def projection():
    return project_season(2026, n_sims=400, rng=np.random.default_rng(3))


@needs_lake
def test_the_schedule_contains_unplayed_games():
    """The whole reason sv_schedule exists: sv_games cannot see the future."""
    standings, remaining, teams = load_season_state(2026)
    assert len(teams) == 30
    assert len(remaining) > 0, "no remaining games — a projection would be vacuous"
    assert remaining.home_team_id.notna().all()


@needs_lake
def test_exactly_twelve_teams_qualify_every_time(projection):
    per_sim = projection.made_playoffs.sum(axis=1)
    assert (per_sim == 2 * PLAYOFF_TEAMS_PER_LEAGUE).all(), "playoff field is the wrong size"


@needs_lake
def test_exactly_six_division_winners_every_time(projection):
    assert (projection.won_division.sum(axis=1) == 6).all()


@needs_lake
def test_one_pennant_per_league_and_one_champion(projection):
    assert (projection.won_pennant.sum(axis=1) == 2).all()
    assert (projection.won_world_series.sum(axis=1) == 1).all()


@needs_lake
def test_division_winners_always_make_the_playoffs(projection):
    """A club cannot win its division and miss the field."""
    assert not (projection.won_division & ~projection.made_playoffs).any()


@needs_lake
def test_champions_always_won_a_pennant(projection):
    assert not (projection.won_world_series & ~projection.won_pennant).any()


@needs_lake
def test_win_totals_are_a_full_season(projection):
    """Every club finishes with a plausible number of wins."""
    totals = projection.win_totals
    assert totals.min() >= 30 and totals.max() <= 130
    # 30 clubs splitting 2,430 games means the league always totals 2,430 wins.
    assert np.allclose(totals.sum(axis=1), totals.sum(axis=1)[0])


@needs_lake
def test_odds_table_is_coherent(projection):
    odds = projection.odds()
    assert len(odds) == 30
    assert odds.ws_pct.sum() == pytest.approx(100.0, abs=0.01)
    assert odds.playoff_pct.sum() == pytest.approx(1200.0, abs=0.01)
    assert (odds.playoff_pct >= odds.pennant_pct - 1e-9).all()
    assert (odds.pennant_pct >= odds.ws_pct - 1e-9).all()


@needs_lake
def test_any_model_family_can_drive_the_projection():
    """The point of the design: swapping the model is one argument."""
    from src.models.baselines import BradleyTerryRidge, PythagoreanBaseline

    for factory in (PythagoreanBaseline, BradleyTerryRidge):
        proj = project_season(2026, model=factory(), n_sims=120, rng=np.random.default_rng(1))
        assert (proj.made_playoffs.sum(axis=1) == 12).all(), factory.__name__
