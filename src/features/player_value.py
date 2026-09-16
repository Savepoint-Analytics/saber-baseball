"""Player value cards — the season marts joined with shrunk talent rates (Phase 2).

``gd_batter_season`` and ``gd_pitcher_season`` carry what a player *did*: raw
counts, rates, RE24, linear-weights runs above average, and the park-adjusted
versions. This module adds what he is probably *like*: the odds-ratio model's
empirical-Bayes rates (``src.simulation.pa_model``), which shrink each outcome
rate toward the league by a regression constant estimated from the season's own
spread of players. A 40-PA call-up with three home runs shows a raw HR rate of
7.5% and a talent rate near the league's 3%; the two are shown side by side
rather than one replacing the other.

The shrunk rates are also turned into a single runs number so they can sit next
to the raw one: ``talent_raa_600`` is each shrunk outcome rate times that season's
linear weight, summed, per 600 PA — a context-neutral, sample-size-aware "runs
above average per full season". The park correction is the same one the marts
apply, on 600 PA.

Why this is Python and not a dbt model: the shrinkage constant is the
beta-binomial estimate in ``PAOutcomeModel``, already tested and used by the
simulator. Re-deriving it in SQL would be a second implementation of the same
number, and the two would drift. Fitting it takes a few seconds per season.

Every output carries the attributions the underlying sources require
(``CLAUDE.md`` §5): Statcast/MLBAM for the plate appearances, Lahman and Chadwick
for the player dimension.

Run::

    python -m src.features.player_value --season 2025                 # leaders
    python -m src.features.player_value --season 2025 --player 660271 # one card
    python -m src.features.player_value --season 2025 --name judge
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source, attribution_block
from src.simulation.form import ON_BASE
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import _lake, load_plate_appearances

app = typer.Typer(add_completion=False, help="Player value cards from the season marts.")

ATTRIBUTION_SOURCES = [Source.STATCAST, Source.MLB_STATS_API, Source.LAHMAN, Source.CHADWICK]

# The rates a card shows from the nine-outcome model. Singles/doubles/triples/ROE
# are shrunk almost entirely to league (k in the thousands) and would only repeat
# the league line.
CARD_OUTCOMES: tuple[str, ...] = ("K", "BB", "HR")

_PLAYER_COLUMNS = (
    "player_id, name_full, bats, throws, primary_position, birth_date, mlb_debut, in_lahman"
)


@dataclass
class TalentRates:
    """Shrunk per-outcome rates for one season, batting and pitching, plus the constants."""

    season: int
    batters: pd.DataFrame
    pitchers: pd.DataFrame
    league: pd.Series
    batter_k: dict[str, float]
    pitcher_k: dict[str, float]

    @classmethod
    def fit(cls, season: int) -> TalentRates:
        """Fit the odds-ratio model on the season's regular-season plate appearances."""
        pa = load_plate_appearances((int(season),))
        if pa.empty:
            raise ValueError(f"no regular-season plate appearances in the lake for {season}")
        model = PAOutcomeModel().fit(pa)
        logger.info(
            "{}: talent rates for {} batters, {} pitchers (k_K={:.0f}, k_BB={:.0f}, k_HR={:.0f})",
            season,
            len(model.batters),
            len(model.pitchers),
            model.batter_k["K"],
            model.batter_k["BB"],
            model.batter_k["HR"],
        )
        return cls(
            season=int(season),
            batters=model.batters,
            pitchers=model.pitchers,
            league=model.league.reindex(list(OUTCOMES)),
            batter_k=model.batter_k,
            pitcher_k=model.pitcher_k,
        )


def _linear_weights(season: int) -> pd.Series:
    con = _lake()
    try:
        lw = con.sql(
            f"select outcome, run_value from main_gold.gd_linear_weights where season = {int(season)}"
        ).df()
    finally:
        con.close()
    return lw.set_index("outcome")["run_value"].reindex(list(OUTCOMES))


def _talent_columns(rates: pd.DataFrame, lw: pd.Series, prefix: str) -> pd.DataFrame:
    """Shrunk rates → card columns: the headline outcomes, on-base, and runs per 600."""
    out = pd.DataFrame(index=rates.index)
    for outcome in CARD_OUTCOMES:
        out[f"{prefix}_{outcome.lower()}_pct"] = rates[outcome]
    out[f"{prefix}_on_base"] = rates[list(ON_BASE)].sum(axis=1)
    out[f"{prefix}_raa_600"] = (rates[list(OUTCOMES)] * lw).sum(axis=1) * 600.0
    return out


def _name_fallback(frame: pd.DataFrame) -> pd.DataFrame:
    """A call-up the Chadwick register has not listed yet is shown by id, not as NaN."""
    missing = frame["name_full"].isna()
    frame.loc[missing, "name_full"] = "MLBAM " + frame.loc[missing, "player_id"].astype(str)
    return frame


def batter_values(
    season: int, season_phase: str = "regular", min_pa: int = 0, talent: TalentRates | None = None
) -> pd.DataFrame:
    """One row per batter: ``gd_batter_season`` + ``sv_players`` + shrunk talent rates."""
    talent = talent or TalentRates.fit(season)
    con = _lake()
    try:
        marts = con.sql(
            f"""
            select b.*, {_PLAYER_COLUMNS.replace("player_id, ", "")}
            from main_gold.gd_batter_season b
            left join main_silver.sv_players p using (player_id)
            where b.season = {int(season)} and b.season_phase = '{season_phase}'
              and b.pa >= {int(min_pa)}
            """
        ).df()
    finally:
        con.close()
    lw = _linear_weights(season)
    cols = _talent_columns(talent.batters, lw, "talent")
    out = _name_fallback(marts.merge(cols, left_on="player_id", right_index=True, how="left"))
    out["talent_raa_600_park_adj"] = (
        out["talent_raa_600"] - (out["park_factor"] - 1.0) * out["lg_runs_per_pa"] * 600.0
    )
    return out.sort_values("lw_raa_park_adj", ascending=False).reset_index(drop=True)


def pitcher_values(
    season: int, season_phase: str = "regular", min_bf: int = 0, talent: TalentRates | None = None
) -> pd.DataFrame:
    """One row per pitcher: ``gd_pitcher_season`` + ``sv_players`` + shrunk talent rates.

    Talent runs are flipped so positive is runs prevented, matching the mart.
    """
    talent = talent or TalentRates.fit(season)
    con = _lake()
    try:
        marts = con.sql(
            f"""
            select b.*, {_PLAYER_COLUMNS.replace("player_id, ", "")}
            from main_gold.gd_pitcher_season b
            left join main_silver.sv_players p using (player_id)
            where b.season = {int(season)} and b.season_phase = '{season_phase}'
              and b.bf >= {int(min_bf)}
            """
        ).df()
    finally:
        con.close()
    lw = _linear_weights(season)
    cols = _talent_columns(talent.pitchers, lw, "talent")
    cols["talent_raa_600"] = -cols["talent_raa_600"]
    out = _name_fallback(marts.merge(cols, left_on="player_id", right_index=True, how="left"))
    out["talent_raa_600_park_adj"] = (
        out["talent_raa_600"] + (out["park_factor"] - 1.0) * out["lg_runs_per_pa"] * 600.0
    )
    return out.sort_values("lw_raa_saved_park_adj", ascending=False).reset_index(drop=True)


def _pct(x: float) -> str:
    return "?" if pd.isna(x) else f"{100 * x:.1f}%"


def _runs(x: float) -> str:
    return "?" if pd.isna(x) else f"{x:+.1f}"


def _wins(x: float) -> str:
    return "?" if pd.isna(x) else f"{x:+.2f}"


def _card_header(row: pd.Series, season: int) -> list[str]:
    bio = " · ".join(
        s
        for s in (
            f"bats {row.bats}" if not pd.isna(row.bats) else "",
            f"throws {row.throws}" if not pd.isna(row.throws) else "",
            f"{row.primary_position}" if not pd.isna(row.primary_position) else "",
            f"born {pd.Timestamp(row.birth_date).date()}" if not pd.isna(row.birth_date) else "",
        )
        if s
    )
    lines = [f"{row.name_full}  ({season}, MLBAM {row.player_id})", f"  {bio}"]
    if not row.in_lahman:
        lines.append("  (not yet in Lahman — bio limited to Chadwick + Statcast)")
    return lines


def format_batter_card(row: pd.Series, talent: TalentRates) -> str:
    """A plain-text value card for one batter-season."""
    lines = _card_header(row, int(row.season))
    lines += [
        "",
        f"  {'':<14}{'raw':>9}{'talent':>9}{'league':>9}",
        f"  {'PA':<14}{int(row.pa):>9}",
        f"  {'K%':<14}{_pct(row.k_pct):>9}{_pct(row.talent_k_pct):>9}{_pct(talent.league['K']):>9}",
        f"  {'BB%':<14}{_pct(row.bb_pct):>9}{_pct(row.talent_bb_pct):>9}{_pct(talent.league['BB']):>9}",
        f"  {'HR%':<14}{_pct(row.hr / row.pa):>9}{_pct(row.talent_hr_pct):>9}{_pct(talent.league['HR']):>9}",
        f"  {'on-base':<14}{_pct(row.on_base_rate):>9}{_pct(row.talent_on_base):>9}"
        f"{_pct(talent.league[list(ON_BASE)].sum()):>9}",
        f"  {'wOBA / xwOBA':<14}{row.woba:>9.3f}{row.xwoba:>9.3f}",
        "",
        f"  {'runs above average':<28}{'raw':>9}{'park-adj':>10}",
        f"  {'RE24 (context-included)':<28}{_runs(row.re24):>9}{_runs(row.re24_park_adj):>10}",
        f"  {'linear weights (neutral)':<28}{_runs(row.lw_raa):>9}{_runs(row.lw_raa_park_adj):>10}",
        f"  {'talent, per 600 PA':<28}{_runs(row.talent_raa_600):>9}"
        f"{_runs(row.talent_raa_600_park_adj):>10}",
        f"  park factor {row.park_factor:.3f} (PA-weighted)",
        "",
        f"  {'wins above average':<28}{'WPA':>9}{'WPA/LI':>10}{'mean LI':>10}",
        f"  {'':<28}{_wins(row.wpa):>9}{_wins(row.wpa_per_li):>10}{row.mean_leverage:>10.2f}",
        "",
        "  talent = odds-ratio rates shrunk to league; regression constants this season: "
        + ", ".join(f"{o} {talent.batter_k[o]:.0f} PA" for o in CARD_OUTCOMES),
    ]
    return "\n".join(lines)


def format_pitcher_card(row: pd.Series, talent: TalentRates) -> str:
    """A plain-text value card for one pitcher-season."""
    lines = _card_header(row, int(row.season))
    lines += [
        "",
        f"  {'':<14}{'raw':>9}{'talent':>9}{'league':>9}",
        f"  {'G / GS / BF':<14}{f'{int(row.games)} / {int(row.starts)} / {int(row.bf)}':>9}",
        f"  {'K%':<14}{_pct(row.k_pct):>9}{_pct(row.talent_k_pct):>9}{_pct(talent.league['K']):>9}",
        f"  {'BB%':<14}{_pct(row.bb_pct):>9}{_pct(row.talent_bb_pct):>9}{_pct(talent.league['BB']):>9}",
        f"  {'HR%':<14}{_pct(row.hr / row.bf):>9}{_pct(row.talent_hr_pct):>9}{_pct(talent.league['HR']):>9}",
        f"  {'on-base allowed':<14}{_pct(row.on_base_allowed):>9}{_pct(row.talent_on_base):>9}"
        f"{_pct(talent.league[list(ON_BASE)].sum()):>9}",
        f"  {'wOBA / xwOBA':<14}{row.woba_allowed:>9.3f}{row.xwoba_allowed:>9.3f}",
        "",
        f"  {'runs saved vs average':<28}{'raw':>9}{'park-adj':>10}",
        f"  {'RE24 (context-included)':<28}{_runs(row.re24_saved):>9}{_runs(row.re24_saved_park_adj):>10}",
        f"  {'linear weights (neutral)':<28}{_runs(row.lw_raa_saved):>9}"
        f"{_runs(row.lw_raa_saved_park_adj):>10}",
        f"  {'talent, per 600 BF':<28}{_runs(row.talent_raa_600):>9}"
        f"{_runs(row.talent_raa_600_park_adj):>10}",
        f"  park factor {row.park_factor:.3f} (BF-weighted)",
        "",
        f"  {'wins above average':<28}{'WPA':>9}{'WPA/LI':>10}{'mean LI':>10}",
        f"  {'':<28}{_wins(row.wpa):>9}{_wins(row.wpa_per_li):>10}{row.mean_leverage:>10.2f}",
        "",
        "  talent = odds-ratio rates shrunk to league; regression constants this season: "
        + ", ".join(f"{o} {talent.pitcher_k[o]:.0f} BF" for o in CARD_OUTCOMES),
    ]
    return "\n".join(lines)


def _leaders(frame: pd.DataFrame, value_col: str, n: int, size_col: str) -> str:
    cols = [
        "name_full",
        size_col,
        "k_pct",
        "bb_pct",
        value_col,
        "talent_raa_600_park_adj",
        "wpa",
        "wpa_per_li",
    ]
    shown = frame.head(n)[cols].copy()
    shown["k_pct"] = (100 * shown["k_pct"]).round(1)
    shown["bb_pct"] = (100 * shown["bb_pct"]).round(1)
    shown[value_col] = shown[value_col].round(1)
    shown["talent_raa_600_park_adj"] = shown["talent_raa_600_park_adj"].round(1)
    shown["wpa"] = shown["wpa"].round(2)
    shown["wpa_per_li"] = shown["wpa_per_li"].round(2)
    shown.columns = [
        "player",
        size_col,
        "K%",
        "BB%",
        "RAA (park-adj)",
        "talent RAA/600",
        "WPA",
        "WPA/LI",
    ]
    return shown.to_string(index=False)


@app.command()
def main(
    season: int = typer.Option(..., "--season"),
    player: int = typer.Option(None, "--player", help="MLBAM id for a single card."),
    name: str = typer.Option(None, "--name", help="Case-insensitive substring of a name."),
    phase: str = typer.Option("regular", "--phase", help="regular | postseason"),
    top: int = typer.Option(15, "--top", help="Leaders to list when no player is given."),
    min_pa: int = typer.Option(300, "--min-pa", help="Minimum PA / BF for the leader lists."),
) -> None:
    talent = TalentRates.fit(season)
    batters = batter_values(season, phase, talent=talent)
    pitchers = pitcher_values(season, phase, talent=talent)

    print(f"\nSAVEPOINT ANALYTICS — PLAYER VALUE, {season} {phase.upper()}\n")
    if player is not None or name:
        if player is not None:
            b = batters[batters.player_id == player]
            p = pitchers[pitchers.player_id == player]
        else:
            needle = name.lower()
            b = batters[batters.name_full.str.lower().str.contains(needle, na=False)]
            p = pitchers[pitchers.name_full.str.lower().str.contains(needle, na=False)]
        if b.empty and p.empty:
            print("  No such player in this season.")
        for row in b.itertuples(index=False):
            print("BATTING\n" + format_batter_card(pd.Series(row._asdict()), talent) + "\n")
        for row in p.itertuples(index=False):
            print("PITCHING\n" + format_pitcher_card(pd.Series(row._asdict()), talent) + "\n")
    else:
        print(f"BATTERS — linear-weights runs above average, park-adjusted (min {min_pa} PA)\n")
        print(_leaders(batters[batters.pa >= min_pa], "lw_raa_park_adj", top, "pa"))
        print(f"\n\nPITCHERS — linear-weights runs saved, park-adjusted (min {min_pa} BF)\n")
        print(_leaders(pitchers[pitchers.bf >= min_pa], "lw_raa_saved_park_adj", top, "bf"))
        print(
            "\nRAA is raw production this season; talent RAA/600 is the same events with "
            "each rate shrunk toward league by a regression constant estimated from the "
            "season's own spread of players, scaled to 600 PA. WPA is in wins; WPA/LI is "
            "the same events at average leverage."
        )

    print("\n" + attribution_block(ATTRIBUTION_SOURCES))


if __name__ == "__main__":
    app()
