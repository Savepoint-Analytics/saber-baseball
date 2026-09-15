-- Silver: batting-order slot and pitcher stint context for every plate appearance.
--
-- Grain: (game_pk, at_bat_number).
--
-- **Batting slot is derived by rule, not inferred.** A team's k-th plate appearance
-- of a game is slot ((k-1) mod 9) + 1, because the order is fixed and a substitute
-- inherits the slot of the man he replaces. That is exact, and it avoids trying to
-- reconstruct substitutions from roster data we do not have.
--
-- **Pitcher stint** columns are what the substitution policy is fitted on: how many
-- batters the current pitcher has faced, which time through the order he is, and
-- whether he was removed after this plate appearance. Removal is observed by the
-- pitcher changing on the next PA of the same half-inning-or-later, so the last PA
-- of a game is not counted as a removal — the game merely ended.
{{ config(materialized='table') }}

with pa as (
    select * from {{ ref('sv_plate_appearances') }}
),

slotted as (
    select
        *,
        (row_number() over (
            partition by game_pk, bat_team_id order by at_bat_number
        ) - 1) % 9 + 1                                as batting_slot,
        row_number() over (
            partition by game_pk, fld_team_id, pitcher order by at_bat_number
        )                                             as pitcher_bf,
        row_number() over (
            partition by game_pk, pitcher, batter order by at_bat_number
        )                                             as times_through_order,
        lead(pitcher) over (
            partition by game_pk, fld_team_id order by at_bat_number
        )                                             as next_pitcher
    from pa
),

first_pitcher as (
    select game_pk, fld_team_id, min(at_bat_number) as first_ab
    from pa group by 1, 2
)

select
    s.game_pk,
    s.game_date,
    s.season,
    s.season_phase,
    s.inning,
    s.at_bat_number,
    s.batter,
    s.pitcher,
    s.bat_team_id,
    s.fld_team_id,
    s.batting_slot,
    s.pitcher_bf,
    s.times_through_order,
    s.outcome,
    s.state_before,
    s.runs_on_play,
    -- Score margin from the fielding team's point of view: a manager's willingness
    -- to leave a starter in depends on whether the game is still close.
    s.outs_before,
    s.next_pitcher is not null and s.next_pitcher <> s.pitcher as removed_after,
    f.first_ab = s.at_bat_number                                as is_first_pa_of_game
from slotted s
join first_pitcher f on f.game_pk = s.game_pk and f.fld_team_id = s.fld_team_id
