"""Pandera schema gates: valid frames pass, malformed frames raise."""

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from src.schemas.bronze import validate_schedule


def _good_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_pk": [745001, 745002],
            "game_date": ["2025-04-01", "2025-04-01"],
            "game_type": ["R", "R"],
            "season": [2025, 2025],
            "status": ["Final", "Final"],
            "home_team_id": [147, 111],
            "away_team_id": [121, 110],
            "home_score": [5, 3],
            "away_score": [2, 4],
        }
    )


def test_valid_schedule_passes():
    out = validate_schedule(_good_schedule())
    assert len(out) == 2


def test_duplicate_game_pk_fails():
    bad = _good_schedule()
    bad.loc[1, "game_pk"] = 745001  # break uniqueness
    with pytest.raises(SchemaErrors):
        validate_schedule(bad)


def test_baselines_sanity():
    from src.models.baselines import log5, pythagorean_expectation

    # Equal teams -> 0.5; better run ratio -> >0.5.
    assert pythagorean_expectation(4, 4) == pytest.approx(0.5)
    assert pythagorean_expectation(5, 3) > 0.5
    assert log5(0.6, 0.6) == pytest.approx(0.5)
    assert log5(0.6, 0.4) > 0.5
