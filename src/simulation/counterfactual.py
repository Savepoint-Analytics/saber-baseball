"""Counterfactual player simulation — Phase 5.

"If this hitter turned 3% of his strikeouts into balls in play, what happens to
his team?" The odds-ratio model holds one nine-outcome rate vector per player;
perturb it, re-run the season's real games with and without the change, and
report the difference in runs and wins. The player is evaluated *in his own
context* -- his lineup, his park, his opponents' actual starters -- rather than
in a vacuum, which is the point of simulating instead of multiplying by a
linear weight.

Two layers, deliberately both shown:

* **Analytic** (``linear_weight_delta``): the change in rates times the season's
  linear weights, per 600 PA. Instant, context-neutral, and the honest first
  answer -- it makes plain that cutting strikeouts is worth almost nothing if
  they become other outs (K -.27 vs OUT -.27) and a great deal if they become
  singles (+.47). *Where* the removed probability goes is the whole question,
  so ``Perturbation`` makes it explicit rather than assuming.
* **Simulated** (``simulate_counterfactual``): the same change inside the game
  engine, over the games the player actually appeared in, with the baseline and
  the counterfactual run on **common random numbers** -- the same seed per game.
  The two arms diverge (one different outcome draw and the RNG streams separate),
  so the pairing is not perfect, but measured on 2025 the arms correlate +0.95
  and the standard error of the difference is **4.7x smaller** than two
  independent runs would give. Reports runs per game, win probability, and wins
  over the games played, with a standard error from the per-game pairs.

**What this is not.** The perturbed rate is treated as the player's new true
talent, not a hot streak. No other player changes, and the manager's decisions
(lineup slot, pulling the starter) are the measured policies, unchanged. The
result is "what this improvement would have been worth in this team, this
season", which is the question a development staff or an agent asks; it is not a
projection of whether the improvement is achievable.

Run::

    python -m src.simulation.counterfactual --season 2025 --player 660271 --change K=-0.03
    python -m src.simulation.counterfactual --season 2025 --player 660271 --change K=-0.03 --absorb 1B
    python -m src.simulation.counterfactual --season 2025 --player 660271 --levers
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source, attribution_block
from src.simulation.context import RunEnvironment, load_game_environments
from src.simulation.form import PitcherFade, PitcherForm
from src.simulation.game_engine import GameSimulator, TeamSetup
from src.simulation.lineup import Bullpen, Lineup, SubstitutionPolicy, load_bullpens, load_lineups
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import TransitionMatrix, _lake, load_plate_appearances

app = typer.Typer(add_completion=False, help="Counterfactual player simulation.")

ATTRIBUTION_SOURCES = [Source.STATCAST, Source.MLB_STATS_API, Source.CHADWICK]
PROPORTIONAL = "proportional"

# One percentage point moved between two outcomes, for the lever table. Each is
# a plausible development target with the destination made explicit.
# Written as batter improvements; lever_table reverses each one for a pitcher.
DEFAULT_LEVERS: tuple[tuple[str, str, str], ...] = (
    ("contact: K <-> balls in play (proportional)", "K", PROPORTIONAL),
    ("contact, all outs: K <-> OUT", "K", "OUT"),
    ("contact, hits: K <-> 1B", "K", "1B"),
    ("discipline: OUT <-> BB", "OUT", "BB"),
    ("power: OUT <-> HR", "OUT", "HR"),
    ("power: 1B <-> 2B", "1B", "2B"),
    ("power: 2B <-> HR", "2B", "HR"),
    ("hard contact: OUT <-> 1B", "OUT", "1B"),
)


@dataclass(frozen=True)
class Perturbation:
    """A change to one player's outcome rates, with the offset made explicit.

    ``changes`` are absolute changes in probability (``{"K": -0.03}`` is three
    points fewer strikeouts per PA). The mass they add or remove is balanced by
    ``absorb``: one named outcome, or ``"proportional"`` to spread it over every
    outcome not in ``changes`` in proportion to the player's current rates.
    """

    player_id: int
    role: str  # "batter" | "pitcher"
    changes: dict[str, float]
    absorb: str = PROPORTIONAL

    def __post_init__(self) -> None:
        bad = set(self.changes) - set(OUTCOMES)
        if bad:
            raise ValueError(f"unknown outcomes {sorted(bad)}; expected {OUTCOMES}")
        if self.absorb != PROPORTIONAL and self.absorb not in OUTCOMES:
            raise ValueError(f"absorb must be an outcome or {PROPORTIONAL!r}")
        if self.absorb in self.changes:
            raise ValueError("the absorbing outcome cannot also be one of the changes")
        if self.role not in ("batter", "pitcher"):
            raise ValueError("role must be 'batter' or 'pitcher'")

    def apply(self, rates: pd.Series) -> pd.Series:
        """The perturbed rate vector; raises if a rate would leave [0, 1]."""
        new = rates.astype(float).copy()
        for outcome, delta in self.changes.items():
            new[outcome] = new[outcome] + delta
        offset = -sum(self.changes.values())
        if self.absorb == PROPORTIONAL:
            others = [o for o in OUTCOMES if o not in self.changes]
            weight = rates[others].astype(float)
            new[others] = new[others] + offset * weight / weight.sum()
        else:
            new[self.absorb] = new[self.absorb] + offset
        if (new < 0).any() or (new > 1).any():
            worst = new.idxmin() if (new < 0).any() else new.idxmax()
            raise ValueError(
                f"perturbation pushes {worst} to {new[worst]:.4f}; "
                f"the player's current rate is {rates[worst]:.4f}"
            )
        return new / new.sum()

    def describe(self) -> str:
        moves = ", ".join(f"{o} {d * 100:+.1f} pp" for o, d in self.changes.items())
        where = "spread proportionally" if self.absorb == PROPORTIONAL else f"into {self.absorb}"
        return f"{moves}, offset {where}"


def perturbed_model(model: PAOutcomeModel, perturbation: Perturbation) -> PAOutcomeModel:
    """A copy of the model with one player's row replaced."""
    table_name = "batters" if perturbation.role == "batter" else "pitchers"
    table = getattr(model, table_name)
    if perturbation.player_id not in table.index:
        raise KeyError(f"{perturbation.role} {perturbation.player_id} not in the fitted model")
    new = copy.copy(model)
    replaced = table.copy()
    replaced.loc[perturbation.player_id] = perturbation.apply(table.loc[perturbation.player_id])
    setattr(new, table_name, replaced)
    return new


def linear_weight_delta(
    rates: pd.Series, perturbation: Perturbation, lw: pd.Series, per: int = 600
) -> float:
    """Runs per ``per`` PA the change is worth at the season's linear weights.

    Positive is good for the player in either role: a pitcher's is the runs he
    prevents.
    """
    new = perturbation.apply(rates)
    delta = float(((new - rates.astype(float)) * lw.reindex(list(OUTCOMES))).sum()) * per
    return delta if perturbation.role == "batter" else -delta


def load_linear_weights(season: int, con: duckdb.DuckDBPyConnection | None = None) -> pd.Series:
    own = con is None
    con = con or _lake()
    try:
        lw = con.sql(
            f"select outcome, run_value from main_gold.gd_linear_weights where season = {int(season)}"
        ).df()
    finally:
        if own:
            con.close()
    return lw.set_index("outcome")["run_value"].reindex(list(OUTCOMES))


def lever_table(
    rates: pd.Series, player_id: int, role: str, lw: pd.Series, size: float = 0.01
) -> pd.DataFrame:
    """Runs per 600 PA for each named one-point lever, ranked best first.

    ``DEFAULT_LEVERS`` are written as *batter* improvements (a strikeout becoming
    a single). For a pitcher the improvement is the same move run backwards --
    a single he allowed becoming a strikeout -- so the source and target are
    swapped rather than the sign merely flipped. Otherwise every row of a
    pitcher's table would describe a way for him to get worse, which is a table
    nobody can act on.
    """
    rows = []
    for label, source, target in DEFAULT_LEVERS:
        delta = -size
        if role == "pitcher":
            if target == PROPORTIONAL:
                # No named target to swap with, so reverse the direction instead:
                # the pitcher *gains* a point of strikeouts, taken proportionally
                # from everything else he allows.
                delta = size
            else:
                source, target = target, source
        p = Perturbation(player_id, role, {source: delta}, absorb=target)
        try:
            runs = linear_weight_delta(rates, p, lw)
        except ValueError:
            runs = float("nan")
        rows.append({"lever": label, "from": source, "to": target, "runs_per_600": runs})
    return pd.DataFrame(rows).sort_values("runs_per_600", ascending=False).reset_index(drop=True)


# --- simulation --------------------------------------------------------------


@dataclass
class CounterfactualResult:
    """Paired baseline / counterfactual outcomes over the player's games."""

    perturbation: Perturbation
    season: int
    games: int
    n_sims: int
    team_id: int
    # Per-game means, baseline and counterfactual, from the team's point of view.
    runs_for: np.ndarray = field(default_factory=lambda: np.zeros((2, 0)))
    runs_against: np.ndarray = field(default_factory=lambda: np.zeros((2, 0)))
    win_prob: np.ndarray = field(default_factory=lambda: np.zeros((2, 0)))

    def _delta(self, arr: np.ndarray) -> tuple[float, float]:
        d = arr[1] - arr[0]
        se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float("nan")
        return float(d.mean()), se

    @property
    def delta_runs_for(self) -> tuple[float, float]:
        return self._delta(self.runs_for)

    @property
    def delta_runs_against(self) -> tuple[float, float]:
        return self._delta(self.runs_against)

    @property
    def delta_win_prob(self) -> tuple[float, float]:
        return self._delta(self.win_prob)

    @property
    def delta_wins(self) -> tuple[float, float]:
        m, se = self.delta_win_prob
        return m * self.games, se * self.games

    def summary(self) -> str:
        rf, rf_se = self.delta_runs_for
        ra, ra_se = self.delta_runs_against
        wp, wp_se = self.delta_win_prob
        w, w_se = self.delta_wins
        return "\n".join(
            [
                f"  games simulated      {self.games} (x {self.n_sims} sims each, paired)",
                f"  runs scored / game   {self.runs_for[0].mean():.3f} -> {self.runs_for[1].mean():.3f}"
                f"   ({rf:+.3f} ± {rf_se:.3f})",
                f"  runs allowed / game  {self.runs_against[0].mean():.3f} -> {self.runs_against[1].mean():.3f}"
                f"   ({ra:+.3f} ± {ra_se:.3f})",
                f"  win probability      {self.win_prob[0].mean():.3f} -> {self.win_prob[1].mean():.3f}"
                f"   ({wp:+.4f} ± {wp_se:.4f})",
                f"  wins over those games                      {w:+.2f} ± {w_se:.2f}",
            ]
        )


def build_simulator(model: PAOutcomeModel, era: str = "2023-2026") -> GameSimulator:
    """The full engine as validated in the methodology, around a given PA model."""
    transitions = TransitionMatrix.load(era)
    return GameSimulator(
        model,
        transitions,
        SubstitutionPolicy.load(era),
        run_environment=RunEnvironment.calibrate(model, transitions, era=era),
        pitcher_form=PitcherForm.calibrate(model, transitions, era=era),
        pitcher_fade=PitcherFade.calibrate(model, era=era),
    )


def player_games(season: int, player_id: int, role: str) -> pd.DataFrame:
    """The regular-season games the player appeared in, with both starters.

    A batter's games are the ones he started; a starting pitcher's are his
    starts; a reliever's are every game his team played, where the bullpen draw
    may or may not summon him -- exactly as it did in reality.
    """
    con = _lake()
    try:
        if role == "batter":
            appearances = f"""
                select distinct game_pk, bat_team_id as team_id
                from main_silver.sv_lineup_slots
                where season = {season} and season_phase = 'regular' and batter = {player_id}
            """
        else:
            appearances = f"""
                select distinct g.game_pk, x.team_id
                from main_silver.sv_statcast_pitcher_game x
                join main_silver.sv_statcast_pitcher_game g
                  on g.team_id = x.team_id and g.season = x.season and g.season_phase = x.season_phase
                where x.season = {season} and x.season_phase = 'regular' and x.pitcher = {player_id}
                  and (x.is_starter and g.game_pk = x.game_pk or not x.is_starter)
            """
        return con.sql(
            f"""
            with mine as ({appearances})
            select g.game_pk, m.team_id, g.home_team_id, g.away_team_id, g.home_win,
                   hs.pitcher as home_starter, aws.pitcher as away_starter
            from mine m
            join main_silver.sv_games g using (game_pk)
            join main_silver.sv_statcast_pitcher_game hs
              on hs.game_pk = g.game_pk and hs.team_id = g.home_team_id and hs.is_starter
            join main_silver.sv_statcast_pitcher_game aws
              on aws.game_pk = g.game_pk and aws.team_id = g.away_team_id and aws.is_starter
            where g.home_win is not null
            order by g.game_pk
            """
        ).df()
    finally:
        con.close()


def simulate_counterfactual(
    season: int,
    perturbation: Perturbation,
    n_games: int | None = None,
    n_sims: int = 300,
    seed: int = 7,
    model: PAOutcomeModel | None = None,
    simulator: GameSimulator | None = None,
    era: str = "2023-2026",
) -> CounterfactualResult:
    """Re-run the player's games with and without the change, paired by seed."""
    model = model or PAOutcomeModel().fit(load_plate_appearances((season,)))
    base_sim = simulator or build_simulator(model, era)
    cf_sim = copy.copy(base_sim)
    cf_sim.pa_model = perturbed_model(model, perturbation)

    games = player_games(season, perturbation.player_id, perturbation.role)
    if games.empty:
        raise ValueError(
            f"no {season} games found for {perturbation.role} {perturbation.player_id}"
        )
    if n_games is not None and len(games) > n_games:
        games = games.sample(n_games, random_state=seed).sort_values("game_pk")
    team_id = int(games.team_id.iloc[0])

    lineups = load_lineups(season)
    bullpens = load_bullpens(season, era=era)
    environments = load_game_environments(season, era=era)

    rows: list[tuple[float, ...]] = []
    for row in games.itertuples():
        setups = _setups(row, lineups, bullpens)
        if setups is None:
            continue
        home, away = setups
        env = environments.get(int(row.game_pk), 1.0)
        pair = []
        for sim in (base_sim, cf_sim):
            # The same seed for both arms: common random numbers.
            rng = np.random.default_rng([seed, int(row.game_pk)])
            res = sim.simulate(home, away, n_sims=n_sims, rng=rng, run_environment=env)
            is_home = int(row.home_team_id) == team_id
            mine, theirs = (
                (res.home_score, res.away_score) if is_home else (res.away_score, res.home_score)
            )
            pair.append((float(mine.mean()), float(theirs.mean()), float((mine > theirs).mean())))
        rows.append((*pair[0], *pair[1]))

    arr = np.array(rows)
    logger.info("{}: {} games x {} sims, baseline and counterfactual", season, len(arr), n_sims)
    return CounterfactualResult(
        perturbation=perturbation,
        season=season,
        games=len(arr),
        n_sims=n_sims,
        team_id=team_id,
        runs_for=np.stack([arr[:, 0], arr[:, 3]]),
        runs_against=np.stack([arr[:, 1], arr[:, 4]]),
        win_prob=np.stack([arr[:, 2], arr[:, 5]]),
    )


def _setups(
    row: object, lineups: dict[tuple[int, int], Lineup], bullpens: dict[int, Bullpen]
) -> tuple[TeamSetup, TeamSetup] | None:
    game_pk, home_id, away_id = int(row.game_pk), int(row.home_team_id), int(row.away_team_id)  # type: ignore[attr-defined]
    home_lineup = lineups.get((game_pk, home_id))
    away_lineup = lineups.get((game_pk, away_id))
    home_pen, away_pen = bullpens.get(home_id), bullpens.get(away_id)
    if not (home_lineup and away_lineup and home_pen and away_pen):
        return None
    return (
        TeamSetup(home_lineup, int(row.home_starter), home_pen),  # type: ignore[attr-defined]
        TeamSetup(away_lineup, int(row.away_starter), away_pen),  # type: ignore[attr-defined]
    )


# --- CLI -----------------------------------------------------------------------


def _parse_changes(specs: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for spec in specs:
        outcome, _, value = spec.partition("=")
        out[outcome.strip().upper()] = float(value)
    return out


def _player_name(player_id: int) -> str:
    con = _lake()
    try:
        row = con.sql(
            f"select name_full from main_silver.sv_players where player_id = {int(player_id)}"
        ).fetchone()
    finally:
        con.close()
    return str(row[0]) if row and row[0] else f"MLBAM {player_id}"


@app.command()
def main(
    season: int = typer.Option(..., "--season"),
    player: int = typer.Option(..., "--player", help="MLBAM id."),
    role: str = typer.Option("batter", "--role", help="batter | pitcher"),
    change: list[str] = typer.Option(
        None, "--change", help="OUTCOME=delta in probability, e.g. K=-0.03. Repeatable."
    ),
    absorb: str = typer.Option(
        PROPORTIONAL, "--absorb", help="Outcome taking the offset, or 'proportional'."
    ),
    levers: bool = typer.Option(
        False, "--levers", help="Rank one-point levers by linear weights only."
    ),
    n_games: int = typer.Option(None, "--n-games", help="Subsample of the player's games."),
    n_sims: int = typer.Option(300, "--n-sims"),
    seed: int = typer.Option(7, "--seed"),
) -> None:
    model = PAOutcomeModel().fit(load_plate_appearances((season,)))
    table = model.batters if role == "batter" else model.pitchers
    if player not in table.index:
        raise typer.BadParameter(f"{role} {player} has no {season} plate appearances")
    rates = table.loc[player]
    lw = load_linear_weights(season)
    name = _player_name(player)

    print(f"\nSAVEPOINT ANALYTICS — COUNTERFACTUAL, {name} ({role}, {season})\n")
    print("  current shrunk rates: " + "  ".join(f"{o} {rates[o] * 100:.1f}%" for o in OUTCOMES))

    if levers or not change:
        print("\nONE-POINT LEVERS — runs per 600 PA at this season's linear weights\n")
        tbl = lever_table(rates, player, role, lw)
        tbl["runs_per_600"] = tbl["runs_per_600"].round(2)
        tbl["move"] = np.where(
            tbl["to"] == PROPORTIONAL,
            np.where(role == "pitcher", "all -> " + tbl["from"], tbl["from"] + " -> all"),
            tbl["from"] + " -> " + tbl["to"],
        )
        print(tbl[["lever", "move", "runs_per_600"]].to_string(index=False))
        print(
            "\nA point of strikeouts is worth almost nothing if it becomes other outs and "
            "a great deal if it becomes hits -- the destination is the whole question. "
            "Pass --change and --absorb to simulate one."
        )
    else:
        p = Perturbation(player, role, _parse_changes(change), absorb=absorb)
        analytic = linear_weight_delta(rates, p, lw)
        print(f"  change: {p.describe()}")
        print(
            "  new rates:            "
            + "  ".join(f"{o} {v * 100:.1f}%" for o, v in p.apply(rates).items())
        )
        print(f"\n  analytic (linear weights, context-neutral): {analytic:+.1f} runs per 600 PA\n")
        print("SIMULATED — the player's actual games, paired baseline vs counterfactual\n")
        result = simulate_counterfactual(
            season, p, n_games=n_games, n_sims=n_sims, seed=seed, model=model
        )
        print(result.summary())

    print("\n" + attribution_block(ATTRIBUTION_SOURCES))


if __name__ == "__main__":
    app()
