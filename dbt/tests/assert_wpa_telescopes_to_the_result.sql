-- Summed over a game from the home side's view, WPA must equal the final win
-- probability (1 or 0) minus the first-PA win probability: every PA's "after" is
-- the next PA's "before", so only the endpoints survive. A game that fails this
-- had a PA valued in the wrong situation -- a score-difference off by one, a
-- half-inning ending scored as live. Rain-shortened games (~5 a season) end
-- before the table says the game is over and are excluded by requiring the last
-- PA's after-value to be a decided game.
with per_game as (
    select
        v.game_pk,
        g.home_win,
        sum(v.wpa * case when v.home_batting then 1 else -1 end)   as home_wpa,
        min_by(v.home_wp_before, v.at_bat_number)                   as wp_start,
        max_by(v.home_wp_after, v.at_bat_number)                    as wp_end
    from {{ ref('gd_pa_run_value') }} v
    join {{ ref('sv_games') }} g using (game_pk)
    where g.home_win is not null
    group by 1, 2
)
select *
from per_game
where wp_end in (0.0, 1.0)
  and (abs(home_wpa - (wp_end - wp_start)) > 1e-9 or wp_end <> home_win)
