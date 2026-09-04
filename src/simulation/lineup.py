"""Lineups, pitcher substitution, and bullpen selection (Phases 4-5).

The third piece of the simulation engine, after the PA outcome model and the base-out
transition matrix. It answers the two questions a game loop asks between plate
appearances: *who bats next* and *who is pitching to him*.

**The substitution policy is measured, not written.** A hand-coded rule ("pull the
starter after 100 pitches") encodes a guess about managerial behaviour, and
managerial behaviour is precisely what moves between eras — the third-time-through
hook tightened from a 0.174 hazard in 2015-2018 to 0.231 in 2019-2022.
``gd_pitcher_removal`` holds the empirical hazard and this module samples it, so the
simulator inherits whatever managers actually did in the era being simulated.

That also matters for validation: the run-expectancy check in
``src.simulation.transitions`` is biased low precisely because it assumes a
league-average pitcher, while in reality the pitcher who has loaded the bases is a
worse one. Simulating real pitchers *and removing them* is what removes that
assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import numpy as np

from src.config import settings

LINEUP_SIZE = 9
# Mirrors the bucketing in gd_pitcher_removal; kept in one place so the lookup keys
# cannot drift away from the table they index.
BF_BUCKETS = (6, 12, 18, 24, 30, 99)
MAX_TTO = 4


def _lake() -> duckdb.DuckDBPyConnection:
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")
    return duckdb.connect(str(path), read_only=True)


def bf_bucket(batters_faced: int) -> int:
    """Bucket a batters-faced count the same way ``gd_pitcher_removal`` does."""
    for bucket in BF_BUCKETS:
        if batters_faced <= bucket:
            return bucket
    return BF_BUCKETS[-1]


@dataclass(frozen=True)
class Lineup:
    """Nine batting slots for one team, in order."""

    batters: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.batters) != LINEUP_SIZE:
            raise ValueError(f"a lineup is {LINEUP_SIZE} batters, got {len(self.batters)}")

    def batter_for(self, pa_index: int) -> int:
        """Batter for a team's ``pa_index``-th plate appearance of the game (0-based).

        The order is cyclic by rule, which is also why batting slot never has to be
        inferred from substitution data we do not have.
        """
        return self.batters[pa_index % LINEUP_SIZE]


@dataclass
class SubstitutionPolicy:
    """Empirical hazard of removing a pitcher after a plate appearance."""

    era: str
    hazard: dict[tuple[bool, int, int], float] = field(default_factory=dict)
    fallback: float = 0.15

    @classmethod
    def load(cls, era: str = "2023-2026") -> SubstitutionPolicy:
        con = _lake()
        try:
            df = con.sql(
                f"""
                select is_starter, tto, bf_bucket, removal_hazard
                from main_gold.gd_pitcher_removal
                where era = '{era}'
                """
            ).df()
        finally:
            con.close()
        if df.empty:
            raise ValueError(f"No removal hazards for era {era!r}")
        hazard = {
            (bool(r.is_starter), int(r.tto), int(r.bf_bucket)): float(r.removal_hazard)
            for r in df.itertuples()
        }
        return cls(era=era, hazard=hazard)

    def removal_probability(self, is_starter: bool, tto: int, batters_faced: int) -> float:
        """P(this pitcher is removed after the plate appearance just completed).

        Cells with fewer than 50 observations are absent from the table by design;
        rather than invent a rate for them, fall back to the pooled reliever-ish
        hazard. Those cells are rare and extreme (a starter 30+ batters in), where
        being approximately right beats being precisely fitted to a dozen games.
        """
        key = (is_starter, min(tto, MAX_TTO), bf_bucket(batters_faced))
        return self.hazard.get(key, self.fallback)

    def should_remove(
        self, rng: np.random.Generator, is_starter: bool, tto: int, batters_faced: int
    ) -> bool:
        return bool(rng.random() < self.removal_probability(is_starter, tto, batters_faced))


# Score bands a manager effectively selects on, and the measured quality of the
# reliever summoned in each (mean season on-base allowed, 2023-2026). Higher is worse.
LEVERAGE_BANDS = ("close", "medium", "blowout")


def leverage_band(margin: int) -> str:
    """Score band a manager is effectively choosing within."""
    if margin <= 2:
        return "close"
    return "medium" if margin <= 5 else "blowout"


@dataclass
class Bullpen:
    """A team's relief options, weighted by usage and by the score when summoned.

    Managers do not pick relievers by availability alone. They pick by the score: the
    best arm enters a one-run game, the mop-up man enters a 9-2 game. Measured across
    2023-2026 that is .3121 on-base allowed in close games against .3261 in blowouts.

    This matters more than its size suggests, because it is a *feedback* effect. A
    lead brings in worse pitching, which extends the lead. Drawing by usage alone --
    as this class originally did -- compresses both tails, producing a simulator with
    too few blowouts *and* too few tight games.

    ``quality`` is each reliever's season on-base allowed. When it and ``targets`` are
    supplied the draw is tilted toward the good or bad end of the staff until the
    expected quality of the pitcher chosen matches what managers actually do.
    """

    team_id: int
    pitchers: np.ndarray
    weights: np.ndarray
    quality: np.ndarray | None = None
    targets: dict[str, float] | None = None
    _tilts: dict[str, float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        total = self.weights.sum()
        if total <= 0:
            raise ValueError(f"bullpen for team {self.team_id} has no usage weight")
        self.weights = self.weights / total
        if self.quality is not None and self.targets is not None:
            self._tilts = {b: self._solve_tilt(self.targets[b]) for b in self.targets}

    def _expected_quality(self, tilt: float) -> float:
        assert self.quality is not None
        w = self.weights * np.exp(-tilt * (self.quality - self.quality.mean()))
        return float(np.dot(w, self.quality) / w.sum())

    def _solve_tilt(self, target: float) -> float:
        """Exponential tilt on usage weights that reproduces a target mean quality.

        Positive tilt favours better relievers. Bounded rather than solved to
        arbitrary precision: an extreme tilt would collapse the staff onto one arm,
        which is a worse error than missing the target by a thousandth.
        """
        assert self.quality is not None
        lo, hi = -50.0, 50.0
        if self._expected_quality(lo) < target or self._expected_quality(hi) > target:
            return 0.0  # target lies outside what this staff can express
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self._expected_quality(mid) > target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def tilt_for(self, margin: int) -> float:
        """Weight tilt for a score margin; 0 when leverage targets are unavailable."""
        return self._tilts.get(leverage_band(margin), 0.0) if self._tilts else 0.0

    def draw(
        self,
        rng: np.random.Generator,
        exclude: set[int] | None = None,
        margin: int | None = None,
    ) -> int:
        """Pick the next reliever, by usage and (when known) by the score.

        Excluding pitchers already used in the game matters: without it a simulated
        team will bring the same reliever back twice in one game, which inflates the
        contribution of its best arm and quietly makes every bullpen look deeper than
        it is.
        """
        mask = np.ones(len(self.pitchers), dtype=bool)
        if exclude:
            mask &= ~np.isin(self.pitchers, list(exclude))
        if not mask.any():  # exhausted: allow reuse rather than fail mid-game
            mask = np.ones(len(self.pitchers), dtype=bool)
        w = self.weights[mask]
        if margin is not None and self._tilts and self.quality is not None:
            tilt = self._tilts.get(leverage_band(margin), 0.0)
            w = w * np.exp(-tilt * (self.quality[mask] - self.quality.mean()))
        return int(rng.choice(self.pitchers[mask], p=w / w.sum()))


def load_lineups(season: int) -> dict[tuple[int, int], Lineup]:
    """Starting lineup per ``(game_pk, team_id)`` for one season.

    Reads the batter who occupied each slot on that slot's first turn, which is the
    starting nine by construction — later occupants are substitutes.
    """
    con = _lake()
    try:
        df = con.sql(
            f"""
            select game_pk, bat_team_id as team_id, batting_slot, batter
            from (
                select *, row_number() over (
                    partition by game_pk, bat_team_id, batting_slot order by at_bat_number
                ) as slot_turn
                from main_silver.sv_lineup_slots
                where season = {season} and season_phase = 'regular'
            )
            where slot_turn = 1
            order by game_pk, team_id, batting_slot
            """
        ).df()
    finally:
        con.close()

    out: dict[tuple[int, int], Lineup] = {}
    for (game_pk, team_id), grp in df.groupby(["game_pk", "team_id"]):
        batters = grp.sort_values("batting_slot").batter.to_numpy(dtype=int)
        if len(batters) == LINEUP_SIZE:
            out[(int(game_pk), int(team_id))] = Lineup(tuple(int(b) for b in batters))
    return out


def load_leverage_targets(era: str = "2023-2026") -> dict[str, float]:
    """Mean reliever quality managers summon in each score band."""
    con = _lake()
    try:
        df = con.sql(
            f"select leverage_band, mean_obr_allowed from main_gold.gd_reliever_leverage "
            f"where era = '{era}'"
        ).df()
    finally:
        con.close()
    return {str(r.leverage_band): float(r.mean_obr_allowed) for r in df.itertuples()}


def load_bullpens(
    season: int, min_appearances: int = 3, era: str = "2023-2026"
) -> dict[int, Bullpen]:
    """Relief options per team, weighted by appearances and tilted by score band."""
    con = _lake()
    try:
        df = con.sql(
            f"""
            select g.team_id, g.pitcher, count(*) as appearances,
                   any_value(q.obr) as quality
            from main_silver.sv_statcast_pitcher_game g
            join (
                select pitcher, avg(case when outcome in ('K','OUT') then 0.0 else 1.0 end) as obr
                from main_silver.sv_plate_appearances
                where season = {season} and season_phase = 'regular'
                group by 1 having count(*) >= 40
            ) q on q.pitcher = g.pitcher
            where not g.is_starter and g.season = {season} and g.season_phase = 'regular'
            group by 1, 2
            having count(*) >= {min_appearances}
            """
        ).df()
    finally:
        con.close()

    targets = load_leverage_targets(era)
    return {
        int(team): Bullpen(
            team_id=int(team),
            pitchers=grp.pitcher.to_numpy(dtype=int),
            weights=grp.appearances.to_numpy(dtype=float),
            quality=grp.quality.to_numpy(dtype=float),
            targets=targets or None,
        )
        for team, grp in df.groupby("team_id")
    }
