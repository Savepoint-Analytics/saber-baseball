"""Player evaluation (Phase 2): RE24 credit, the player dimension, the season marts.

The pure-pandas tests pin the RE24 arithmetic on a hand-built inning. The
lake-backed tests check the two things dbt cannot: that the pandas mirror and the
SQL produce the same matrix, and that the value-card join lands on the people it
should -- a leaderboard that puts Aaron Judge tenth is wrong even if every schema
test passes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import settings
from src.features.run_expectancy import (
    N_STATES,
    build_re24,
    build_wpa_table,
    load_run_expectancy,
    re24_credit,
    wpa_credit,
)

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")


def _state(outs: int, b1: bool = False, b2: bool = False, b3: bool = False) -> int:
    return outs * 8 + int(b1) + 2 * int(b2) + 4 * int(b3)


def _matrix() -> pd.Series:
    """A plausible 24-state matrix: falls with outs, rises with runners."""
    re = {}
    for outs in range(3):
        for b1 in (False, True):
            for b2 in (False, True):
                for b3 in (False, True):
                    re[_state(outs, b1, b2, b3)] = (0.5 + 0.4 * b1 + 0.6 * b2 + 0.8 * b3) * (
                        1.0 - 0.3 * outs
                    )
    return pd.Series(re).sort_index()


def _inning() -> pd.DataFrame:
    """Leadoff HR, single, strikeout, double (runner scores), K, groundout."""
    rows = [
        # state_before, outcome, runs, outs_after, b1_after, b2_after, b3_after
        (_state(0), "HR", 1, 0, False, False, False),
        (_state(0), "1B", 0, 0, True, False, False),
        (_state(0, b1=True), "K", 0, 1, True, False, False),
        (_state(1, b1=True), "2B", 1, 1, False, True, False),
        (_state(1, b2=True), "K", 0, 2, False, True, False),
        (_state(2, b2=True), "OUT", 0, 3, False, False, False),
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "state_before",
            "outcome",
            "runs_on_play",
            "outs_after",
            "b1_after",
            "b2_after",
            "b3_after",
        ],
    )
    return frame


def test_bases_empty_home_run_is_worth_exactly_one_run():
    """RE(after) == RE(before) for a leadoff HR, so the credit is the run itself."""
    credit = re24_credit(_inning(), _matrix())
    assert credit.iloc[0] == pytest.approx(1.0)


def test_third_out_zeroes_the_after_state():
    re = _matrix()
    credit = re24_credit(_inning(), re)
    # Last PA: RE(after) = 0, no runs, so credit is -RE(before).
    assert credit.iloc[-1] == pytest.approx(-re[_state(2, b2=True)])


def test_re24_telescopes_to_runs_minus_starting_expectancy():
    """Summed over a complete half-inning, only the endpoints survive."""
    inning = _inning()
    re = _matrix()
    total = re24_credit(inning, re).sum()
    assert total == pytest.approx(inning.runs_on_play.sum() - re[_state(0)])


def test_build_re24_averages_complete_half_innings_only():
    pa = pd.DataFrame(
        {
            "state_before": [0, 0, 0, 5],
            "runs_to_end_of_inning": [1.0, 0.0, 9.0, 2.0],
            "in_complete_half_inning": [True, True, False, True],
        }
    )
    re = build_re24(pa)
    assert len(re) == N_STATES
    assert re[0] == pytest.approx(0.5)  # the 9-run truncated inning is excluded
    assert re[5] == pytest.approx(2.0)
    assert np.isnan(re[23])  # unobserved state stays missing, not zero


def test_build_re24_rejects_a_frame_without_the_needed_columns():
    with pytest.raises(ValueError, match="missing columns"):
        build_re24(pd.DataFrame({"state_before": [0]}))


@needs_lake
def test_pandas_wpa_matches_the_dbt_wpa_on_a_game():
    from src.features.win_probability import build_for_season

    game = _lake_sql(
        """
        select p.inning, p.regulation_innings, p.inning_topbot, p.home_score_before,
               p.away_score_before, p.state_before, p.runs_on_play,
               lead(p.inning) over g as next_inning, lead(p.inning_topbot) over g as next_half,
               lead(p.home_score_before) over g as next_home_score,
               lead(p.away_score_before) over g as next_away_score,
               lead(p.state_before) over g as next_state,
               v.wpa
        from main_silver.sv_plate_appearances p
        join main_gold.gd_pa_run_value v using (game_pk, at_bat_number)
        where p.game_pk = (select min(game_pk) from main_silver.sv_games where season = 2025)
        window g as (order by p.at_bat_number)
        order by p.at_bat_number
        """
    )
    credit = wpa_credit(game, build_for_season(2025))
    np.testing.assert_allclose(credit.to_numpy(), game.wpa.to_numpy(), atol=1e-9)
    assert len(build_wpa_table(2025)) == 20500


# --- lake-backed -------------------------------------------------------------


def _lake_sql(sql: str) -> pd.DataFrame:
    import duckdb

    con = duckdb.connect(str(LAKE), read_only=True)
    try:
        return con.sql(sql).df()
    finally:
        con.close()


@needs_lake
def test_pandas_matrix_matches_the_dbt_matrix():
    """The SQL and the pandas mirror must agree, or a notebook and a mart would differ."""
    pa = _lake_sql(
        """
        select state_before, runs_to_end_of_inning, in_complete_half_inning
        from main_silver.sv_plate_appearances
        where season = 2025 and season_phase = 'regular'
        """
    )
    from_pandas = build_re24(pa)
    from_dbt = load_run_expectancy(2025)
    np.testing.assert_allclose(from_pandas.to_numpy(), from_dbt.to_numpy(), rtol=0, atol=1e-9)


@needs_lake
def test_pandas_credit_matches_the_dbt_credit_on_a_game():
    game = _lake_sql(
        """
        select p.state_before, p.outs_after, p.b1_after, p.b2_after, p.b3_after,
               p.runs_on_play, v.re24
        from main_silver.sv_plate_appearances p
        join main_gold.gd_pa_run_value v using (game_pk, at_bat_number)
        where p.season = 2025 and p.season_phase = 'regular'
          and p.game_pk = (select min(game_pk) from main_silver.sv_games where season = 2025)
        order by p.at_bat_number
        """
    )
    assert len(game) > 50
    credit = re24_credit(game, load_run_expectancy(2025))
    np.testing.assert_allclose(credit.to_numpy(), game.re24.to_numpy(), atol=1e-9)


@needs_lake
def test_player_dimension_resolves_the_ids_the_marts_use():
    """Every batter and pitcher in the season marts has a dimension row, even before
    the Chadwick register lists him -- and almost all of them have a name."""
    rows = _lake_sql(
        """
        select count(*) as n,
               count(*) filter (where p.player_id is null) as orphans,
               count(*) filter (where p.name_full is null) as unnamed
        from (
            select player_id from main_gold.gd_batter_season
            union
            select player_id from main_gold.gd_pitcher_season
        ) m
        left join main_silver.sv_players p using (player_id)
        """
    ).iloc[0]
    assert int(rows.orphans) == 0
    assert int(rows.unnamed) < 0.02 * int(rows.n)


@needs_lake
def test_player_dimension_knows_who_aaron_judge_is():
    row = _lake_sql("select * from main_silver.sv_players where player_id = 592450").iloc[0]
    assert row.name_full == "Aaron Judge"
    assert row.bats == "R" and row.throws == "R"
    assert row.bats_statcast == "R"
    assert row.key_bbref == "judgeaa01"
    assert row.primary_position == "OF"
    assert row.in_lahman


@needs_lake
def test_linear_weights_land_on_the_textbook_values():
    """Measured, not assumed -- and they reproduce the published weights."""
    lw = _lake_sql(
        "select outcome, run_value from main_gold.gd_linear_weights where season = 2025"
    ).set_index("outcome")["run_value"]
    assert 0.43 < lw["1B"] < 0.52
    assert 0.72 < lw["2B"] < 0.85
    assert 0.95 < lw["3B"] < 1.15
    assert 1.33 < lw["HR"] < 1.45
    assert 0.28 < lw["BB"] < 0.36
    assert -0.32 < lw["K"] < -0.24


@needs_lake
def test_value_cards_join_talent_rates_and_rank_sensibly():
    from src.features.player_value import TalentRates, batter_values, pitcher_values

    talent = TalentRates.fit(2024)
    batters = batter_values(2024, talent=talent, min_pa=300)
    assert batters.name_full.iloc[0] == "Aaron Judge"
    judge = batters.iloc[0]
    # Shrinkage pulls toward league, never past it.
    assert talent.league["HR"] < judge.talent_hr_pct < judge.hr / judge.pa
    assert judge.talent_raa_600 < judge.lw_raa / judge.pa * 600
    assert judge.talent_raa_600 > 0

    pitchers = pitcher_values(2024, talent=talent, min_bf=300)
    assert pitchers.lw_raa_saved_park_adj.iloc[0] > 30
    assert (pitchers.talent_on_base < 1).all()
    assert pitchers.name_full.notna().all()


@needs_lake
def test_a_tiny_sample_is_shrunk_almost_to_league():
    from src.features.player_value import TalentRates, batter_values

    talent = TalentRates.fit(2024)
    small = batter_values(2024, talent=talent)
    small = small[small.pa <= 10]
    assert len(small) > 20
    # Within two points of league on-base for everyone with <= 10 PA, against a
    # raw spread that runs from 0% to 100% at that sample size.
    league_ob = talent.league[["BB", "HBP", "1B", "2B", "3B", "HR", "ROE"]].sum()
    assert (small.talent_on_base - league_ob).abs().max() < 0.02
    assert (small.on_base_rate - league_ob).abs().max() > 0.3
